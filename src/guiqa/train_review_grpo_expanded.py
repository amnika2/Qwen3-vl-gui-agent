"""Train the expanded screenshot review pool with class-balanced Dr. GRPO."""

import argparse
from collections import Counter, defaultdict
import importlib.metadata as metadata
import json
from pathlib import Path
import random
import time

import yaml

from .artifacts import write_json
from .review_facts import facts_prompt
from .rl_review_diagnostic import read_rows, validate_training_reviews
from .rl_review_expanded_reward import ExpandedFactsReward, expanded_rules
from .rl_reward_rules import load_reward_rules
from .train_review_grpo import ROOT, adapter_summary, file_hash, grpo_config, prepare_sample, training_dataset
from .train_review_grpo_pilot import pilot_trainer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/rl-review-grpo-expanded-v1.yaml")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume-from", type=Path)
    args = parser.parse_args()
    args.config, args.output = args.config.resolve(), args.output.resolve()
    samples, schedule, model_config, settings, training, plan = prepare(args)
    if args.resume_from is None:
        args.output.mkdir(parents=True)
        write_json(args.output / "plan.json", plan)
    train(args, samples, schedule, model_config, settings, training)


def prepare(args):
    if args.output.exists() and args.resume_from is None:
        raise FileExistsError(args.output)
    if metadata.version("trl") != "0.27.2":
        raise ValueError("expanded GRPO uses TRL 0.27.2")
    settings = yaml.safe_load(args.config.read_text())
    if settings["max_steps"] < 1 or settings["save_steps"] < 1:
        raise ValueError("max_steps and save_steps must be positive")
    if (settings["groups_per_update"], settings["num_generations"]) != (3, 4):
        raise ValueError("each update uses pass/fail/blocked, with four candidates per question")
    model_config = yaml.safe_load((ROOT / settings["model_config"]).read_text())
    dataset = ROOT / settings["dataset"]
    manifest = json.loads((dataset / "manifest.json").read_text())
    if (file_hash(dataset / "train.jsonl") != manifest["data_sha256"]
            or file_hash(dataset / "validation.jsonl") != manifest["validation_sha256"]):
        raise ValueError("RL rows differ from the dataset manifest")
    rows = validate_training_reviews(read_rows(dataset / "train.jsonl"),
        read_rows(dataset / "validation.jsonl"), yaml.safe_load((ROOT / "configs/sft-split.yaml").read_text()))
    catalog = load_reward_rules(ROOT / settings["reward_rules"])
    samples = []
    for row in rows:
        sample = prepare_sample(row, dataset, catalog)
        sample["reward_rules"] = expanded_rules(row, dataset, catalog)
        sample["prompt"][0]["content"] = [{"type": "text", "text": facts_prompt(sample["profile"])}]
        sample["case_id"] = row["case_id"]
        samples.append(sample)
    if {sample["gold"]["verdict"] for sample in samples} != {"pass", "fail", "blocked"}:
        raise ValueError("balanced training requires all three labels")
    schedule = balanced_schedule(samples, settings["max_steps"], settings["seed"])
    batch = settings["groups_per_update"] * settings["num_generations"]
    training = grpo_config(args.output, max_steps=settings["max_steps"],
        gradient_accumulation_steps=batch, generation_batch_size=batch,
        num_generations=settings["num_generations"], save_strategy="steps",
        save_steps=settings["save_steps"], save_total_limit=None,
        learning_rate=settings["learning_rate"], beta=settings["beta"],
        max_completion_length=settings["max_completion_length"], temperature=settings["temperature"],
        seed=settings["seed"], data_seed=settings["seed"])
    source_names = ("src/guiqa/train_review_grpo_expanded.py", "src/guiqa/rl_review_expanded_reward.py",
                    "src/guiqa/train_review_grpo.py", "src/guiqa/train_review_grpo_pilot.py",
                    "src/guiqa/rl_review_reward.py", "src/guiqa/rl_reward_rules.py",
                    "src/guiqa/review_facts.py", "src/guiqa/review_contract.py")
    identity_paths = [args.config, ROOT / settings["model_config"], ROOT / settings["reward_rules"],
                      dataset / "manifest.json", dataset / "train.jsonl", dataset / "validation.jsonl",
                      *(ROOT / name for name in source_names)]
    source_adapter = ROOT / model_config["adapter_path"]
    plan = {"settings": settings, "model_config": model_config,
        "pool_samples": len(samples), "pool_labels": dict(Counter(sample["gold"]["verdict"] for sample in samples)),
        "schedule": [{"id": samples[index]["id"], "case_id": samples[index]["case_id"],
                      "label": samples[index]["gold"]["verdict"]} for index in schedule],
        "scheduled_question_exposures": len(schedule),
        "scheduled_unique_questions": len({samples[index]["id"] for index in schedule}),
        "sampling": "One pass, fail and blocked question per update; rotate applications within each label; seed-fixed bucket order.",
        "training_arguments": training.to_dict(),
        "identity": {str(path.relative_to(ROOT)): file_hash(path) for path in identity_paths},
        "initial_adapter_sha256": {name: file_hash(source_adapter / name)
                                    for name in ("adapter_config.json", "adapter_model.safetensors")},
        "training_reward_rules": {sample["id"]: sample["reward_rules"] for sample in samples},
        "versions": {name: metadata.version(name) for name in ("torch", "transformers", "peft", "trl", "accelerate")},
        "selection": "Fixed final optimizer step; validation and test are used only for evaluation."}
    if args.resume_from is not None:
        previous = json.loads((args.output / "plan.json").read_text())
        if any(plan[key] != previous[key] for key in ("identity", "schedule", "initial_adapter_sha256")):
            raise ValueError("resume must use the original data, schedule and initialization")
    return samples, schedule, model_config, settings, training, plan


def balanced_schedule(samples, steps, seed):
    buckets = defaultdict(lambda: defaultdict(list))
    for index, sample in enumerate(samples):
        buckets[sample["gold"]["verdict"]][sample["case_id"]].append(index)
    rng = random.Random(seed)
    applications = {}
    for label, groups in buckets.items():
        applications[label] = sorted(groups)
        rng.shuffle(applications[label])
        for indices in groups.values():
            rng.shuffle(indices)
    cursors, schedule = Counter(), []
    for step in range(steps):
        for label in ("pass", "fail", "blocked"):
            cases = applications[label]
            case = cases[step % len(cases)]
            indices = buckets[label][case]
            schedule.append(indices[cursors[label, case] % len(indices)])
            cursors[label, case] += 1
    return schedule


def expanded_trainer():
    class ExpandedTrainer(pilot_trainer()):
        def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
            import torch

            with torch.autograd.graph.save_on_cpu(pin_memory=True):
                return super().compute_loss(model, inputs, return_outputs, num_items_in_batch)

    return ExpandedTrainer


def training_monitor(output, reward):
    from transformers import TrainerCallback

    class TrainingMonitor(TrainerCallback):
        def on_step_end(self, args, state, control, **kwargs):
            groups = reward.groups[-3:]
            row = {"optimizer_step": state.global_step,
                "sample_ids": [group["sample_id"] for group in groups],
                "groups": groups}
            with (output / "update-steps.jsonl").open("a") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(json.dumps({"optimizer_step": state.global_step,
                              "maximum_optimizer_steps": args.max_steps,
                              "sampled_groups": len(reward.groups),
                              "task_differential_groups": sum(group["trusted_reward_difference"] for group in groups),
                              "unknown_candidates": sum(group["unknown_candidates"] for group in groups)},
                             ensure_ascii=False), flush=True)

    return TrainingMonitor()


def train(args, samples, schedule, config, settings, training):
    import torch
    from peft import PeftModel
    from transformers import AutoProcessor, Qwen3VLForConditionalGeneration, set_seed

    started = time.monotonic()
    torch.set_num_threads(4)
    set_seed(settings["seed"])
    processor = AutoProcessor.from_pretrained(ROOT / config["model_path"], local_files_only=True,
        min_pixels=200704, max_pixels=config["max_pixels"])
    base = Qwen3VLForConditionalGeneration.from_pretrained(ROOT / config["model_path"],
        local_files_only=True, dtype=torch.bfloat16, attn_implementation="sdpa")
    base.config.use_cache = False
    model = PeftModel.from_pretrained(base, ROOT / config["adapter_path"], is_trainable=True)
    reward = ExpandedFactsReward(samples, args.output,
        {processor.tokenizer.eos_token_id, processor.tokenizer.pad_token_id})
    if args.resume_from is not None:
        reward.groups = read_rows(args.output / "reward-groups.jsonl")
    trainer = expanded_trainer()(model=model, args=training, processing_class=processor,
        train_dataset=training_dataset([samples[index] for index in schedule]),
        reward_funcs=reward, callbacks=[training_monitor(args.output, reward)])
    initial_actor = adapter_summary(model, "default")["sha256"]
    initial_reference = adapter_summary(model, "ref")["sha256"]
    vision_trainable = sum(parameter.numel() for name, parameter in model.named_parameters()
                           if ".visual." in name and parameter.requires_grad)
    write_json(args.output / "model-state.json", {
        "initial_actor_sha256": initial_actor, "initial_reference_sha256": initial_reference,
        "vision_trainable_parameters": vision_trainable})
    metrics = trainer.train(resume_from_checkpoint=str(args.resume_from) if args.resume_from else None).metrics
    model.save_pretrained(args.output / "adapter", selected_adapters=["default"])
    trainer.save_state()
    completion = {"completed": trainer.state.global_step == settings["max_steps"],
        "optimizer_steps": trainer.state.global_step, "initial_adapter": config["adapter_path"],
        "adapter": str(args.output / "adapter"),
        "adapter_sha256": file_hash(args.output / "adapter/adapter_model.safetensors"),
        "pool_samples": len(samples), "question_exposures": len(schedule),
        "unique_scheduled_questions": len({samples[index]["id"] for index in schedule}),
        "reward_groups": len(reward.groups),
        "task_differential_groups": sum(group["trusted_reward_difference"] for group in reward.groups),
        "all_unknown_groups": sum(group["all_unknown"] for group in reward.groups),
        "same_reward_groups": sum(group["same_score"] for group in reward.groups),
        "actor_changed": adapter_summary(model, "default")["sha256"] != initial_actor,
        "reference_unchanged": adapter_summary(model, "ref")["sha256"] == initial_reference,
        "reference_equal_to_initial_sft": initial_reference == initial_actor,
        "vision_trainable_parameters": vision_trainable,
        "metrics": metrics,
        "elapsed_seconds": time.monotonic() - started,
        "peak_allocated_mib": torch.cuda.max_memory_allocated() / 2**20}
    write_json(args.output / "completion.json", completion)
    print(json.dumps(completion, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
