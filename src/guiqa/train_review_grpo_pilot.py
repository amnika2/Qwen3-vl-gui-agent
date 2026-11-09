"""Run a checkpointed GRPO pilot on a fixed training curriculum."""

import argparse
import importlib.metadata as metadata
import json
import math
import os
import random
import time
import traceback
from collections import Counter
from pathlib import Path

import yaml

from .artifacts import write_json
from .rl_review_diagnostic import read_rows, validate_training_reviews
from .rl_reward_rules import load_reward_rules
from .train_review_grpo import (
    ROOT, ReviewReward, adapter_files, adapter_summary, cpu_input_probe, file_hash,
    grpo_config, prepare_sample, sequential_generation_trainer, training_dataset,
)


def main():
    args = arguments()
    samples, model_config, settings, training, plan = prepare_pilot(args)
    if not args.resume:
        args.output.mkdir(parents=True)
        write_json(args.output / "plan.json", plan)
    if args.check_only:
        write_json(args.output / "input-probe.json", cpu_input_probe(samples, model_config))
        print(json.dumps(plan, ensure_ascii=False, indent=2), flush=True)
        return
    run_training(args, samples, model_config, settings, training, plan)


def run_training(args, samples, config, settings, training, plan):
    import torch
    from peft import PeftModel
    from transformers import AutoProcessor, Qwen3VLForConditionalGeneration, set_seed

    started = time.monotonic()
    torch.set_num_threads(4)
    set_seed(settings["seed"])
    processor = AutoProcessor.from_pretrained(
        ROOT / config["model_path"], local_files_only=True,
        min_pixels=256 * 28 * 28, max_pixels=config["max_pixels"])
    base = Qwen3VLForConditionalGeneration.from_pretrained(
        ROOT / config["model_path"], local_files_only=True, dtype=torch.bfloat16,
        attn_implementation="sdpa")
    base.config.use_cache = False
    model = PeftModel.from_pretrained(base, ROOT / config["adapter_path"], is_trainable=True)
    before = adapter_summary(model, "default")
    if not args.resume:
        write_json(args.output / "default-before.json", before)
    end_tokens = {processor.tokenizer.eos_token_id, processor.tokenizer.pad_token_id}
    if settings.get("task_reward") == "binary_verified":
        from .rl_review_retry import BinaryReviewReward, retry_trainer

        reward = BinaryReviewReward(samples, args.output, end_tokens)
        trainer_class = retry_trainer(reward, settings["max_generation_attempts"])
    else:
        reward = ReviewReward(samples, args.output, end_tokens)
        trainer_class = pilot_trainer()
    if args.resume:
        reward.groups = read_rows(args.output / "reward-groups.jsonl")
    monitor = pilot_monitor(args.output, reward, before["sha256"])
    trainer = trainer_class(
        model=model, args=training, processing_class=processor,
        train_dataset=training_dataset(samples), reward_funcs=reward, callbacks=[monitor])
    reference = adapter_summary(model, "ref")
    if reference["sha256"] != before["sha256"]:
        raise ValueError("the reference must start as an exact copy of v4")
    if any(parameter.requires_grad for name, parameter in model.named_parameters()
           if ".visual." in name or ".ref." in name):
        raise ValueError("vision and reference parameters must remain frozen")
    write_json(args.output / "reference-before.json", reference)
    checkpoint = last_checkpoint(args.output) if args.resume else None
    try:
        result = trainer.train(resume_from_checkpoint=checkpoint)
        after = adapter_summary(model, "default")
        reference_after = adapter_summary(model, "ref")
        model.save_pretrained(args.output / "adapter", selected_adapters=["default"])
        trainer.save_state()
        write_json(args.output / "default-after.json", after)
        write_json(args.output / "reference-after.json", reference_after)
        verified = {
            "reference_unchanged": reference_after["sha256"] == before["sha256"],
            "original_v4_disk_unchanged": adapter_files(ROOT / config["adapter_path"]) == plan["v4_adapter_files"],
            "actor_changed": after["sha256"] != before["sha256"],
            "trusted_task_gradient_update_seen": any(row["trusted_task_gradient_update"] for row in monitor.updates),
            "all_gradients_finite": all(row["finite"] for row in monitor.gradients),
            "requested_steps_completed": trainer.state.global_step == settings["max_steps"],
        }
        completion = {
            "completed": True, "training_verified": all(verified.values()),
            "optimizer_steps": trainer.state.global_step, "verification": verified,
            "reward_groups": len(reward.groups),
            "dropped_groups": sum(row["dropped_untrusted_group"] for row in reward.groups),
            "differential_groups": sum(row["trusted_reward_difference"] for row in reward.groups),
            "trusted_task_gradient_updates": sum(row["trusted_task_gradient_update"] for row in monitor.updates),
            "metrics": result.metrics, "elapsed_seconds": time.monotonic() - started,
            "peak_allocated_mib": torch.cuda.max_memory_allocated() / 2**20,
            "quality_evaluation": "Independent 39-review checkpoint evaluations are saved separately.",
        }
        write_json(args.output / "completion.json", completion)
        print(json.dumps(completion, ensure_ascii=False, indent=2), flush=True)
        if not completion["training_verified"]:
            raise RuntimeError("pilot finished without satisfying training verification")
    except Exception:
        (args.output / "error.txt").write_text(traceback.format_exc())
        write_json(args.output / "failure.json", {
            "optimizer_steps": trainer.state.global_step, "reward_groups": len(reward.groups),
            "elapsed_seconds": time.monotonic() - started,
            "last_complete_checkpoint": last_checkpoint(args.output, required=False),
            "original_v4_disk_unchanged": adapter_files(ROOT / config["adapter_path"]) == plan["v4_adapter_files"]})
        raise


def prepare_pilot(args):
    if args.output.exists() and not args.resume:
        raise FileExistsError("pilot output already exists: " + str(args.output))
    if metadata.version("trl") != "0.27.2":
        raise ValueError("the pilot requires the verified TRL 0.27.2 environment")
    settings = yaml.safe_load(args.config.read_text())
    dataset = (ROOT / settings["dataset"]).resolve()
    model_path = ROOT / settings["model_config"]
    rules_path = ROOT / settings["reward_rules"]
    config = yaml.safe_load(model_path.read_text())
    adapter = (ROOT / config["adapter_path"]).resolve()
    if args.output == adapter or adapter in args.output.parents:
        raise ValueError("pilot output must remain outside the preserved v4 adapter")
    split_path = ROOT / "configs/sft-split.yaml"
    training_rows = validate_training_reviews(
        read_rows(dataset / "train.jsonl"), read_rows(dataset / "validation.jsonl"),
        yaml.safe_load(split_path.read_text()))
    selected = select_rows(training_rows, settings["source_run_ids"])
    if settings.get("shuffle_once", True):
        random.Random(settings["seed"]).shuffle(selected)
    catalog = load_reward_rules(rules_path)
    samples = [prepare_sample(row, dataset, catalog) for row in selected]
    training = grpo_config(
        args.output, cpu=args.check_only, max_steps=settings["max_steps"],
        save_strategy="steps", save_steps=settings["save_steps"], save_total_limit=None,
        shuffle_dataset=False, learning_rate=settings["learning_rate"], beta=settings["beta"],
        seed=settings["seed"], data_seed=settings["seed"])
    source_names = (
        "src/guiqa/train_review_grpo_pilot.py", "scripts/train_review_grpo_pilot.py",
        "src/guiqa/train_review_grpo.py", "src/guiqa/rl_review_reward.py",
        "src/guiqa/rl_reward_rules.py", "src/guiqa/review_contract.py",
        "src/guiqa/rl_review_diagnostic.py")
    if settings.get("task_reward") == "binary_verified":
        if not isinstance(settings["max_generation_attempts"], int) or settings["max_generation_attempts"] < 1:
            raise ValueError("binary retry requires a positive max_generation_attempts")
        source_names += ("src/guiqa/rl_review_retry.py",)
    identity_paths = [args.config, model_path, rules_path, split_path,
                      dataset / "train.jsonl", dataset / "validation.jsonl",
                      *(ROOT / name for name in source_names)]
    identity = {str(path.resolve()): file_hash(path) for path in identity_paths}
    image_hashes = {str(path): file_hash(path) for sample in samples for path in sample["images"]}
    plan = {
        "mode": "cpu_preflight" if args.check_only else "grpo_pilot",
        "identity": identity, "image_sha256": image_hashes, "settings": settings,
        "sample_ids": [sample["id"] for sample in samples],
        "sample_order_policy": ("One seed-fixed shuffle" if settings.get("shuffle_once", True) else "Configured order")
            + " followed by a fixed repeated order, including on resume.",
        "training_label_counts": dict(Counter(sample["gold"]["verdict"] for sample in samples)),
        "training_case_counts": dict(Counter(row["case_id"] for row in selected)),
        "training_arguments": training.to_dict(),
        "v4_adapter_files": adapter_files(adapter),
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "evaluation": {"partition": "validation", "samples": 39, "save_every_steps": settings["save_steps"]},
        "versions": {name: metadata.version(name) for name in
                     ("torch", "transformers", "peft", "trl", "accelerate", "datasets")},
    }
    if args.resume:
        previous = json.loads((args.output / "plan.json").read_text())
        if any(previous[key] != plan[key] for key in ("identity", "image_sha256", "v4_adapter_files")):
            raise ValueError("resume inputs differ from the frozen pilot plan")
        plan = previous
    return samples, config, settings, training, plan


def select_rows(rows, source_run_ids):
    if not source_run_ids or len(source_run_ids) != len(set(source_run_ids)):
        raise ValueError("pilot source_run_ids must be nonempty and unique")
    selected = []
    for run_id in source_run_ids:
        matched = [row for row in rows if row["derived_provenance"]["source_run_id"] == run_id]
        if len(matched) != 1:
            raise ValueError("pilot source run must match exactly one training review: " + run_id)
        selected.append(matched[0])
    return selected


def pilot_trainer():
    class PilotTrainer(sequential_generation_trainer()):
        def _load_from_checkpoint(self, resume_from_checkpoint, model=None):
            actor = self.model if model is None else model
            # Transformers 4.57.6 sees ref/ and otherwise skips the root default adapter.
            actor.load_adapter(resume_from_checkpoint, "default", is_trainable=True)
            actor.load_adapter(str(Path(resume_from_checkpoint) / "ref"), "ref", is_trainable=False)
            actor.set_adapter("default")

    return PilotTrainer


def pilot_monitor(output, reward, reference_sha256):
    from transformers import TrainerCallback

    class PilotMonitor(TrainerCallback):
        def __init__(self):
            self.gradients, self.updates = [], []
            self.previous_actor_sha256 = None

        def on_train_begin(self, args, state, control, model, **kwargs):
            self.previous_actor_sha256 = adapter_summary(model, "default")["sha256"]

        def on_pre_optimizer_step(self, args, state, control, model, **kwargs):
            import torch

            gradients = [parameter.grad.detach() for name, parameter in model.named_parameters()
                         if ".default." in name and parameter.grad is not None]
            row = {"optimizer_step": state.global_step + 1,
                   "finite": all(bool(torch.isfinite(value).all().item()) for value in gradients),
                   "l2": math.sqrt(sum(value.float().square().sum().item() for value in gradients))}
            self.gradients.append(row)
            _append(output / "gradient-steps.jsonl", row)
            if not row["finite"]:
                raise ValueError("adapter gradients contain non-finite values")

        def on_step_end(self, args, state, control, model, **kwargs):
            current = adapter_summary(model, "default")
            if adapter_summary(model, "ref")["sha256"] != reference_sha256:
                raise ValueError("the frozen reference adapter changed")
            changed = current["sha256"] != self.previous_actor_sha256
            group = reward.groups[-1]
            row = {"optimizer_step": state.global_step, "group": group["index"],
                   "actor_changed": changed, "gradient_l2": self.gradients[-1]["l2"],
                   "trusted_task_gradient_update": changed and self.gradients[-1]["l2"] > 0
                       and group["trusted_reward_difference"],
                   "reference_unchanged": True, "actor_sha256": current["sha256"]}
            self.updates.append(row)
            _append(output / "update-steps.jsonl", row)
            self.previous_actor_sha256 = current["sha256"]

        def on_save(self, args, state, control, **kwargs):
            checkpoint = output / f"checkpoint-{state.global_step}"
            write_json(checkpoint / "ready.json", {
                "optimizer_step": state.global_step, "actor_sha256": self.previous_actor_sha256,
                "adapter_file_sha256": file_hash(checkpoint / "adapter_model.safetensors")})

    return PilotMonitor()


def last_checkpoint(output, required=True):
    checkpoints = sorted((path for path in output.glob("checkpoint-*") if (path / "ready.json").exists()),
                         key=lambda path: int(path.name.split("-")[-1]))
    if checkpoints:
        return str(checkpoints[-1])
    if required:
        raise ValueError("no complete checkpoint is available to resume")
    return None


def _append(path, row):
    with path.open("a") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/rl-review-grpo-pilot.yaml")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    args.config, args.output = args.config.resolve(), args.output.resolve()
    return args
