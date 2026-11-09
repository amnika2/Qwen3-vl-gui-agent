"""One or two GRPO updates on two fixed training reviews, with frozen v4 reference."""

import argparse
import copy
import hashlib
import json
import math
import os
import time
import traceback
from pathlib import Path

import yaml

from .artifacts import write_json
from .rl_review_reward import resolve_training_reward, score_review, validate_gold
from .rl_reward_rules import load_reward_rules, rules_for_sample

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_PREFIXES = ("20261107T161416Z-47cd0702", "20261107T150537Z-1a7a33a9")


def main():
    args = arguments()
    samples, config, plan = prepare_smoke(args)
    args.output.mkdir(parents=True)
    write_json(args.output / "plan.json", plan)
    if args.check_only:
        print(json.dumps(plan, ensure_ascii=False, indent=2), flush=True)
        return
    run_smoke(args.output, samples, config, plan)


def run_smoke(output, samples, config, plan):
    import torch
    from peft import PeftModel
    from transformers import AutoProcessor, Qwen3VLForConditionalGeneration, set_seed

    started = time.monotonic()
    torch.set_num_threads(4)
    set_seed(42)
    processor = AutoProcessor.from_pretrained(
        ROOT / config["model_path"], local_files_only=True,
        min_pixels=256 * 28 * 28, max_pixels=config["max_pixels"])
    base = Qwen3VLForConditionalGeneration.from_pretrained(
        ROOT / config["model_path"], local_files_only=True, dtype=torch.bfloat16,
        attn_implementation="sdpa")
    base.config.use_cache = False
    model = PeftModel.from_pretrained(base, ROOT / config["adapter_path"], is_trainable=True)
    before = adapter_summary(model, "default")
    write_json(output / "default-before.json", before)
    reward = ReviewReward(samples, output,
                          {processor.tokenizer.eos_token_id, processor.tokenizer.pad_token_id})
    monitor = smoke_monitor(before, output, reward)
    trainer = sequential_generation_trainer()(model=model, args=grpo_config(output), processing_class=processor,
        train_dataset=training_dataset(samples), reward_funcs=reward, callbacks=[monitor])
    reference = adapter_summary(model, "ref")
    if reference["sha256"] != before["sha256"]:
        raise ValueError("the reference adapter is not an exact copy of the selected v4 adapter")
    visual = [name for name, parameter in model.named_parameters()
              if ".visual." in name and parameter.requires_grad]
    if visual:
        raise ValueError("vision parameters must remain frozen")
    if any(parameter.requires_grad for name, parameter in model.named_parameters() if ".ref." in name):
        raise ValueError("the reference adapter must not be trainable outside reference inference")
    write_json(output / "reference-before.json", reference)
    monitor.reference_sha256 = reference["sha256"]
    try:
        result = trainer.train()
        after = adapter_summary(model, "default")
        reference_after = adapter_summary(model, "ref")
        unchanged = adapter_files(ROOT / config["adapter_path"]) == plan["v4_adapter_files"]
        write_json(output / "default-after.json", after)
        write_json(output / "reference-after.json", reference_after)
        verified = {
            "finite_nonzero_gradient": any(row["finite"] and row["l2"] > 0 for row in monitor.gradients),
            "default_adapter_changed": after["sha256"] != before["sha256"],
            "reference_equal_to_initial_v4": reference["sha256"] == before["sha256"],
            "reference_unchanged": reference_after["sha256"] == reference["sha256"],
            "original_v4_disk_unchanged": unchanged,
            "trusted_differential_reward_seen": any(row["trusted_reward_difference"] for row in reward.groups),
            "trusted_group_nonzero_gradient_update": any(row["trusted_group_nonzero_gradient_update"]
                                                          for row in monitor.updates),
            "vision_frozen": not visual,
        }
        model.save_pretrained(output / "adapter", selected_adapters=["default"])
        trainer.save_state()
        completion = {"completed": True, "smoke_passed": all(verified.values()),
                      "optimizer_steps": trainer.state.global_step, "maximum_optimizer_steps": 2,
                      "verification": verified, "reward_groups": reward.groups,
                      "gradient_steps": monitor.gradients, "metrics": result.metrics,
                      "update_steps": monitor.updates,
                      "elapsed_seconds": time.monotonic() - started,
                      "peak_allocated_mib": torch.cuda.max_memory_allocated() / 2**20,
                      "peak_reserved_mib": torch.cuda.max_memory_reserved() / 2**20,
                      "quality_improvement_evaluated": False}
        write_json(output / "completion.json", completion)
        print(json.dumps(completion, ensure_ascii=False, indent=2), flush=True)
        if not completion["smoke_passed"]:
            raise RuntimeError("GRPO finished its fixed budget without satisfying the smoke verification")
    except Exception:
        (output / "error.txt").write_text(traceback.format_exc())
        if not (output / "completion.json").exists():
            write_json(output / "completion.json", {"completed": False,
                "optimizer_steps": trainer.state.global_step, "reward_groups": reward.groups,
                "gradient_steps": monitor.gradients, "error": "see error.txt",
                "update_steps": monitor.updates,
                "elapsed_seconds": time.monotonic() - started,
                "original_v4_disk_unchanged": adapter_files(ROOT / config["adapter_path"]) == plan["v4_adapter_files"]})
        raise


class ReviewReward:
    __name__ = "independent_review_facts"

    def __init__(self, samples, output, end_token_ids):
        self.samples = {sample["id"]: sample for sample in samples}
        self.output = output
        self.end_token_ids = end_token_ids
        self.groups = []

    def __call__(self, completions, sample_id, completion_ids, **kwargs):
        if len(set(sample_id)) != 1 or len(completions) != 4 or len(completion_ids) != 4:
            raise ValueError("this smoke expects one complete same-question G4 reward group")
        sample = self.samples[sample_id[0]]
        raw = [completion[0]["content"] for completion in completions]
        scores, resolved = self.score_answers(sample, raw, completion_ids)
        rewards, dropped = trusted_group_rewards(resolved)
        group = {"index": len(self.groups), "sample_id": sample_id[0],
                 "dropped_untrusted_group": dropped,
                 "source_none_candidates": sum(score["reward"] is None for score in scores),
                 "untrusted_candidates": sum(item["reward"] is None for item in resolved),
                 "certain_negative_candidates": sum(item["certain_negative"] for item in resolved),
                 "truncated_candidates": sum("transport_error" in score for score in scores),
                 "positive_candidates": sum(item["reward"] is not None and item["reward"] > 0 for item in resolved),
                 "negative_candidates": sum(item["reward"] is not None and item["reward"] < 0 for item in resolved),
                 "rewards_returned_to_trainer": rewards,
                 "reward_range": max(rewards) - min(rewards),
                 "trusted_reward_difference": not dropped and max(rewards) > min(rewards)}
        self.groups.append(group)
        with (self.output / "reward-groups.jsonl").open("a") as handle:
            handle.write(json.dumps(group, ensure_ascii=False) + "\n")
        with (self.output / "rollouts.jsonl").open("a") as handle:
            for index, (answer, score, value, resolution) in enumerate(zip(raw, scores, rewards, resolved)):
                handle.write(json.dumps({"group": group["index"], "sample_id": sample_id[0],
                    "candidate": index, "raw": answer, "score": score,
                    "reward_returned_to_trainer": value,
                    "certain_negative": resolution["certain_negative"],
                    "proof_paths": resolution["proof_paths"],
                    "dropped_untrusted_group": dropped}, ensure_ascii=False) + "\n")
        return rewards

    def score_answers(self, sample, raw, completion_ids):
        scores = [score_review(answer, sample["gold"], sample["steps"], sample["profile"],
                               rules=sample["reward_rules"], truncated=ids[-1] not in self.end_token_ids)
                  for answer, ids in zip(raw, completion_ids)]
        return scores, [resolve_training_reward(score) for score in scores]


def trusted_group_rewards(scores):
    dropped = any(score["reward"] is None for score in scores)
    return ([0.0] * len(scores) if dropped else [score["reward"] for score in scores]), dropped


def sequential_generation_trainer():
    from trl import GRPOTrainer

    class SequentialGenerationTrainer(GRPOTrainer):
        def _generate_single_turn(self, prompts):
            # A simultaneous G4 exceeded the 24 GiB GPU; retain G4 while generating one response at a time.
            prompt_ids, completion_ids = [], []
            for prompt in prompts:
                one_prompt, one_completion, _, _ = super()._generate_single_turn([prompt])
                prompt_ids.extend(one_prompt)
                completion_ids.extend(one_completion)
            return prompt_ids, completion_ids, None, {}

    return SequentialGenerationTrainer


def smoke_monitor(before, output, reward):
    from transformers import TrainerCallback

    class SmokeMonitor(TrainerCallback):
        def __init__(self):
            self.gradients = []
            self.updates = []
            self.reference_sha256 = None
            self.previous_actor_sha256 = before["sha256"]

        def on_pre_optimizer_step(self, args, state, control, model, **kwargs):
            import torch

            gradients = [parameter.grad.detach() for name, parameter in model.named_parameters()
                         if ".default." in name and parameter.grad is not None]
            finite = all(bool(torch.isfinite(gradient).all().item()) for gradient in gradients)
            row = {"optimizer_step": state.global_step + 1, "finite": finite,
                   "parameter_tensors_with_gradient": len(gradients),
                   "l2": math.sqrt(sum(gradient.float().square().sum().item() for gradient in gradients))}
            self.gradients.append(row)
            with (output / "gradient-steps.jsonl").open("a") as handle:
                handle.write(json.dumps(row) + "\n")
            if not finite:
                raise ValueError("adapter gradients contain non-finite values")

        def on_step_end(self, args, state, control, model, **kwargs):
            current = adapter_summary(model, "default")
            reference = adapter_summary(model, "ref")
            if reference["sha256"] != self.reference_sha256:
                raise ValueError("the frozen reference adapter changed during an update")
            updated = current["sha256"] != self.previous_actor_sha256
            trusted_difference = reward.groups[-1]["trusted_reward_difference"]
            passed = (updated and self.gradients[-1]["finite"] and self.gradients[-1]["l2"] > 0
                      and trusted_difference)
            row = {"optimizer_step": state.global_step, "group": reward.groups[-1]["index"],
                   "actor_changed_this_update": updated,
                   "trusted_group_nonzero_gradient_update": passed,
                   "reference_unchanged": True}
            self.updates.append(row)
            with (output / "update-steps.jsonl").open("a") as handle:
                handle.write(json.dumps(row) + "\n")
            self.previous_actor_sha256 = current["sha256"]
            if passed:
                control.should_training_stop = True
            return control

    return SmokeMonitor()


def adapter_summary(model, adapter):
    import torch

    digest, count, tensors = hashlib.sha256(), 0, []
    for name, parameter in model.named_parameters():
        if f".{adapter}." not in name:
            continue
        canonical = name.replace(f".{adapter}.", ".adapter.")
        value = parameter.detach().cpu().contiguous()
        data = value.view(torch.uint8).numpy().tobytes()
        digest.update(canonical.encode())
        digest.update(data)
        count += value.numel()
        tensors.append({"name": canonical, "shape": list(value.shape), "dtype": str(value.dtype),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "l2": value.double().norm().item()})
    if not tensors:
        raise ValueError("adapter has no parameters: " + adapter)
    return {"sha256": digest.hexdigest(), "parameters": count, "tensors": tensors}


def grpo_config(output, cpu=False, **overrides):
    from trl import GRPOConfig

    options = dict(output_dir=str(output), max_steps=2, per_device_train_batch_size=1,
        gradient_accumulation_steps=4, generation_batch_size=4, num_generations=4,
        shuffle_dataset=False, num_iterations=1, learning_rate=2e-6, lr_scheduler_type="constant",
        weight_decay=0.0, beta=0.02, loss_type="dr_grpo", scale_rewards=False,
        max_completion_length=1024, temperature=0.8, top_p=1.0, top_k=0,
        bf16=True, tf32=not cpu, use_cpu=cpu, gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False}, disable_dropout=True,
        use_vllm=False, remove_unused_columns=False, mask_truncated_completions=True,
        eval_strategy="no", save_strategy="no", logging_steps=1,
        report_to="none", seed=42, data_seed=42, dataloader_num_workers=0)
    return GRPOConfig(**(options | overrides))


def training_dataset(samples):
    from datasets import Dataset
    from PIL import Image

    return Dataset.from_list([{"sample_id": sample["id"], "prompt": sample["prompt"],
        "images": [Image.open(path).convert("RGB") for path in sample["images"]]} for sample in samples])


def cpu_input_probe(samples, config):
    from transformers import AutoProcessor
    from trl.data_utils import prepare_multimodal_messages

    dataset = training_dataset(samples)
    processor = AutoProcessor.from_pretrained(ROOT / config["model_path"], local_files_only=True,
                                              min_pixels=256 * 28 * 28, max_pixels=config["max_pixels"])
    results = []
    for row in dataset:
        prompt = prepare_multimodal_messages(row["prompt"], row["images"])
        inputs = processor.apply_chat_template(conversation=[prompt], tokenize=True,
            add_generation_prompt=True, padding=True, return_dict=True, return_tensors="pt")
        if inputs["image_grid_thw"].shape[0] != len(row["images"]):
            raise ValueError("processor image grids do not match the supplied real screenshots")
        results.append({"sample_id": row["sample_id"], "images": len(row["images"]),
                        "tensors": {key: {"shape": list(value.shape), "device": str(value.device)}
                                    for key, value in inputs.items()}})
    return {"dataset_columns": dataset.column_names, "samples": results}


def prepare_smoke(args):
    import importlib.metadata as metadata
    import torch

    if args.output.exists():
        raise FileExistsError("smoke output already exists: " + str(args.output))
    if metadata.version("trl") != "0.27.2":
        raise ValueError("the smoke environment must use the verified TRL 0.27.2")
    config = yaml.safe_load(args.config.read_text())
    adapter = (ROOT / config["adapter_path"]).resolve()
    if args.output == adapter or adapter in args.output.parents:
        raise ValueError("smoke output must remain outside the preserved v4 adapter")
    rows = [json.loads(line) for line in (args.dataset / "train.jsonl").read_text().splitlines()]
    reward_rules = load_reward_rules(args.reward_rules)
    samples = []
    for prefix in SAMPLE_PREFIXES:
        matched = [row for row in rows if row["id"].startswith(prefix)]
        if len(matched) != 1 or matched[0]["data_split"] != "train":
            raise ValueError("the fixed smoke sample must occur exactly once in the training partition: " + prefix)
        row = matched[0]
        samples.append(prepare_sample(row, args.dataset, reward_rules))
    training = grpo_config(args.output, cpu=args.check_only)
    plan = {"mode": "cpu_preflight" if args.check_only else "gpu_smoke",
            "sample_order": [sample["id"] for sample in samples],
            "images_per_sample": [len(sample["images"]) for sample in samples],
            "maximum_optimizer_steps": 2, "stop_after_first_verified_update": True,
            "microbatch": training.per_device_train_batch_size,
            "gradient_accumulation_steps": training.gradient_accumulation_steps,
            "generation_batch_size": training.generation_batch_size,
            "steps_per_generation": training.steps_per_generation, "num_generations": training.num_generations,
            "loss_type": training.loss_type, "scale_rewards": training.scale_rewards,
            "learning_rate": training.learning_rate, "beta": training.beta,
            "max_completion_length": training.max_completion_length,
            "image_processing": {"min_pixels": 256 * 28 * 28, "max_pixels": config["max_pixels"]},
            "sampling_parameters": {"temperature": training.temperature,
                                    "top_p": training.top_p, "top_k": training.top_k},
            "schema_logits_processor": False, "provisional_rewards_used": False,
            "truncated_reward_policy": "Use the TRL 0.27.2 completion_ids end-token test; truncated candidates remain reward=None and mask the full G4 reward group.",
            "generation_microbatch": 1,
            "generation_change_reason": "The first smoke exceeded 24 GiB while generating the Typesetter G4 simultaneously.",
            "certain_negative_policy": "A verified mismatch with other unresolved evidence receives exactly -1, with proof paths. Literal-value proofs require their own verified image citation. No provisional reward is used.",
            "untrusted_group_policy": "After certain-negative resolution, any remaining reward=None drops the complete same-question G4 task reward to zero.",
            "sampling": "Standard Transformers GRPO generation without a custom schema logits processor.",
            "dataset_sha256": file_hash(args.dataset / "train.jsonl"),
            "source_code_sha256": {name: file_hash(ROOT / name) for name in
                ("src/guiqa/train_review_grpo.py", "scripts/smoke_review_grpo.py",
                 "src/guiqa/rl_review_reward.py", "src/guiqa/rl_reward_rules.py", "src/guiqa/review_contract.py")},
            "reward_rules": str(args.reward_rules), "reward_rules_sha256": file_hash(args.reward_rules),
            "config": config, "config_sha256": file_hash(args.config),
            "image_sha256": {str(path): file_hash(path) for sample in samples for path in sample["images"]},
            "v4_adapter_files": adapter_files(ROOT / config["adapter_path"]),
            "cuda_initialized_during_preflight": torch.cuda.is_initialized(),
            "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
            "versions": {name: metadata.version(name) for name in
                         ("torch", "torchvision", "transformers", "peft", "trl", "accelerate", "datasets")}}
    if args.check_only:
        plan["cpu_multimodal_input_probe"] = cpu_input_probe(samples, config)
        plan["cuda_initialized_after_input_probe"] = torch.cuda.is_initialized()
    return samples, config, plan


def prepare_sample(row, dataset, reward_rules):
    prompt = copy.deepcopy(row["messages"][:-1])
    images = []
    for message in prompt:
        for item in message["content"]:
            if item["type"] == "image":
                images.append((dataset / item.pop("image")).resolve())
    steps = {frame["step"] for frame in row["review_frames"]}
    if len(images) != len(steps) or not images:
        raise ValueError("review images and selected steps differ")
    profile = row.get("review_profile", "persistence")
    gold = validate_gold(json.loads(row["messages"][-1]["content"]), steps, profile)
    return {"id": row["id"], "prompt": prompt, "images": images,
            "steps": steps, "profile": profile, "gold": gold,
            "reward_rules": rules_for_sample(reward_rules, row, dataset)}


def adapter_files(directory):
    return {str(path.relative_to(directory)): file_hash(path)
            for path in sorted(directory.rglob("*")) if path.is_file()}


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "datasets/rl-review-pilot-v1")
    parser.add_argument("--config", type=Path, default=ROOT / "configs/agent-review-evidence-v4-selected.yaml")
    parser.add_argument("--reward-rules", type=Path, default=ROOT / "configs/rl-review-reward-rules.yaml")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    args.dataset, args.config, args.output = args.dataset.resolve(), args.config.resolve(), args.output.resolve()
    args.reward_rules = args.reward_rules.resolve()
    return args
