"""Accumulate two verified, distinct-question G4 groups before a GRPO update."""

import argparse
import copy
import importlib.metadata as metadata
import json
import math
import os
import shutil
import time
from collections import Counter
from pathlib import Path

import yaml

from .artifacts import write_json
from .review_facts import facts_prompt, parse_facts
from .rl_review_diagnostic import read_rows, validate_training_reviews
from .rl_review_retry import BinaryReviewReward, group_eligible
from .rl_reward_rules import load_reward_rules
from .train_review_grpo import (ROOT, adapter_files, adapter_summary, cpu_input_probe,
                                file_hash, grpo_config, prepare_sample, training_dataset)
from .train_review_grpo_pilot import pilot_trainer, select_rows


class InsufficientEffectiveGroups(RuntimeError):
    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/rl-review-grpo-grouped-v5.yaml")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    args.config, args.output = args.config.resolve(), args.output.resolve()
    samples, config, settings, training, plan = prepare_grouped(args)
    args.output.mkdir(parents=True)
    write_json(args.output / "plan.json", plan)
    save_sources(args.output, plan["source_sha256"])
    if args.check_only:
        write_json(args.output / "input-probe.json", cpu_input_probe(samples, config))
        print(json.dumps(plan, ensure_ascii=False, indent=2), flush=True)
        return
    run_grouped(args.output, samples, config, settings, training, plan)


def prepare_grouped(args):
    if args.output.exists():
        raise FileExistsError("grouped GRPO output already exists")
    if metadata.version("trl") != "0.27.2":
        raise ValueError("grouped GRPO uses the verified TRL 0.27.2 hooks")
    settings = yaml.safe_load(args.config.read_text())
    if (settings["groups_per_update"] != 2 or settings["max_generation_attempts"] != 2
            or not 1 <= settings["max_steps"] <= 2 or settings["max_samples_per_update"] > 12):
        raise ValueError("this trial requires two G4 groups, at most two attempts and two updates")
    config_path = ROOT / settings["model_config"]
    config = yaml.safe_load(config_path.read_text())
    v4_adapter = ROOT / config["adapter_path"]
    config["adapter_path"] = settings["adapter_path"]
    config["review_format"] = "evidence_facts_v1"
    adapter = (ROOT / config["adapter_path"]).resolve()
    if args.output == adapter or adapter in args.output.parents:
        raise ValueError("grouped output must be separate from its initial adapter")
    dataset = ROOT / settings["dataset"]
    split_path = ROOT / "configs/sft-split.yaml"
    rows = validate_training_reviews(read_rows(dataset / "train.jsonl"), read_rows(dataset / "validation.jsonl"),
                                     yaml.safe_load(split_path.read_text()))
    curriculum_path = ROOT / settings["curriculum_config"]
    curriculum = yaml.safe_load(curriculum_path.read_text())["source_run_ids"]
    selected = select_rows(rows, curriculum)
    catalog = load_reward_rules(ROOT / settings["reward_rules"])
    samples = []
    for row in selected:
        sample = prepare_sample(row, dataset, catalog)
        sample["prompt"][0]["content"] = [{"type": "text", "text": facts_prompt(sample["profile"])}]
        sample["case_id"] = row["case_id"]
        samples.append(sample)
    training = grpo_config(args.output, cpu=args.check_only, max_steps=settings["max_steps"],
        gradient_accumulation_steps=8, generation_batch_size=8, num_generations=4,
        save_strategy="steps", save_steps=1, save_total_limit=None,
        learning_rate=settings["learning_rate"], beta=settings["beta"],
        seed=settings["seed"], data_seed=settings["seed"])
    sources = [args.config, config_path, curriculum_path, split_path,
               ROOT / settings["reward_rules"], dataset / "train.jsonl", dataset / "validation.jsonl"]
    sources += [ROOT / name for name in ("src/guiqa/train_review_grpo_grouped.py",
        "scripts/train_review_grpo_grouped.py", "src/guiqa/train_review_grpo_pilot.py",
        "src/guiqa/train_review_grpo.py", "src/guiqa/rl_review_retry.py", "src/guiqa/rl_review_reward.py",
        "src/guiqa/rl_reward_rules.py", "src/guiqa/review_contract.py", "src/guiqa/review_facts.py")]
    plan = {"mode": "cpu_preflight" if args.check_only else "grouped_grpo",
        "settings": settings, "config": config, "training_arguments": training.to_dict(),
        "sample_ids": [sample["id"] for sample in samples],
        "label_counts": dict(Counter(sample["gold"]["verdict"] for sample in samples)),
        "case_counts": dict(Counter(sample["case_id"] for sample in samples)),
        "source_sha256": {str(path.relative_to(ROOT)): file_hash(path) for path in sources},
        "image_sha256": {str(path): file_hash(path) for sample in samples for path in sample["images"]},
        "preserved_adapters": {str(path.resolve()): adapter_files(path) for path in (v4_adapter, adapter)},
        "versions": {name: metadata.version(name) for name in ("torch", "transformers", "peft", "trl", "accelerate")},
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "sampling": "Sequential G4 sampling without schema constraints; two attempts per question; no optimizer step until two distinct eligible questions are collected.",
        "selection": "Prefer different applications within the fixed question scan budget; retain whole G4 groups.",
        "reward": "Verified binary facts after deterministic explanation rendering; unverified candidates make the G4 ineligible.",
        "development_or_holdout_used_for_training": False}
    return samples, config, settings, training, plan


class GroupedFactsReward(BinaryReviewReward):
    __name__ = "verified_grouped_review_facts"

    def __init__(self, samples, output, end_token_ids):
        super().__init__(samples, output, end_token_ids)
        self.selected = None
        self.batches = []

    def score_answers(self, sample, raw, completion_ids):
        rendered = []
        for answer in raw:
            try:
                rendered.append(json.dumps(parse_facts(answer, sample["steps"], sample["profile"])))
            except (ValueError, KeyError, TypeError):
                rendered.append("")
        return super().score_answers(sample, rendered, completion_ids)

    def __call__(self, completions, sample_id, completion_ids, **kwargs):
        expected_ids = [group["sample_id"] for group in self.selected for _ in range(4)]
        expected_tokens = [ids for group in self.selected for ids in group["generated"][1]]
        if sample_id != expected_ids or completion_ids != expected_tokens:
            raise ValueError("training candidates differ from the selected same-actor G4 tokens")
        values, indices = [], []
        for offset, selected in zip(range(0, 8, 4), self.selected):
            sample = self.samples[selected["sample_id"]]
            raw = [completion[0]["content"] for completion in completions[offset:offset + 4]]
            scores, resolutions = self.score_answers(sample, raw, completion_ids[offset:offset + 4])
            rewards = [item["reward"] for item in resolutions]
            if not group_eligible(rewards) or rewards != selected["binary_rewards"]:
                raise ValueError("selected G4 no longer has its verified positive/negative comparison")
            group = {"index": len(self.groups), "optimizer_step": selected["optimizer_step"],
                "sample_id": sample["id"], "case_id": sample["case_id"], "label": sample["gold"]["verdict"],
                "sampling_attempt": selected["attempt_id"], "rewards": rewards,
                "cached_completion_tokens_match": True, "trusted_reward_difference": True}
            self.groups.append(group)
            indices.append(group["index"])
            append_jsonl(self.output / "reward-groups.jsonl", group)
            for index, (answer, score, reward) in enumerate(zip(raw, scores, rewards)):
                append_jsonl(self.output / "rollouts.jsonl", {"group": group["index"], "candidate": index,
                    "sample_id": sample["id"], "raw": answer, "score": score,
                    "reward_returned_to_trainer": reward, "completion_ids": completion_ids[offset + index]})
            values.extend(rewards)
        self.batches.append({"optimizer_step": self.selected[0]["optimizer_step"], "groups": indices,
            "sample_ids": [group["sample_id"] for group in self.selected],
            "case_ids": [group["case_id"] for group in self.selected],
            "labels": [group["label"] for group in self.selected], "cached_completion_tokens_match": True})
        self.selected = None
        return values


def choose_groups(eligible):
    if len(eligible) < 2:
        return eligible
    return [eligible[0], next((group for group in eligible[1:]
                              if group["case_id"] != eligible[0]["case_id"]), eligible[1])]


def grouped_trainer(reward, pool, max_attempts=2, max_samples=12):
    class GroupedTrainer(pilot_trainer()):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.pool_cursor = 0
            self.cached_generation = None

        def _generate_and_score_completions(self, inputs):
            from trl.data_utils import prepare_multimodal_messages

            if self._step % self.args.gradient_accumulation_steps:
                raise ValueError("effective groups must be collected at the start of an optimizer update")
            step = self.state.global_step + 1
            eligible, attempts = [], []
            for _ in range(min(max_samples, len(pool))):
                sample_input = pool[self.pool_cursor % len(pool)]
                self.pool_cursor += 1
                sample = reward.samples[sample_input["sample_id"]]
                for attempt in range(max_attempts):
                    prompts = [prepare_multimodal_messages(sample_input["prompt"], sample_input["images"])
                               for _ in range(4)]
                    generated = super()._generate_single_turn(prompts)
                    preview = reward.preview(sample["id"], generated[1], self.processing_class)
                    record = {"attempt_id": f"{step}:{len(attempts)}", "optimizer_step": step,
                        "sample_id": sample["id"], "case_id": sample["case_id"],
                        "label": sample["gold"]["verdict"], "attempt": attempt + 1,
                        "reason": "eligible" if preview["eligible"] else "no_verified_positive_negative_comparison",
                        **preview, "completion_ids": generated[1]}
                    attempts.append(record)
                    append_jsonl(reward.output / "sampling-attempts.jsonl", record)
                    if preview["eligible"]:
                        eligible.append({**record, "generated": generated, "input": sample_input})
                        break
                if len(eligible) >= 2 and any(group["case_id"] != eligible[0]["case_id"] for group in eligible[1:]):
                    break
            selected = choose_groups(eligible)
            selected_ids = {group["attempt_id"] for group in selected} if len(selected) == 2 else set()
            for attempt in attempts:
                append_jsonl(reward.output / "sampling-selections.jsonl", {
                    "attempt_id": attempt["attempt_id"], "optimizer_step": step,
                    "selected": attempt["attempt_id"] in selected_ids,
                    "reason": ("selected_whole_G4" if attempt["attempt_id"] in selected_ids else
                               "insufficient_effective_questions" if len(selected) < 2 else
                               "prefer_distinct_application_or_ineligible")})
            if len(selected) < 2:
                append_jsonl(reward.output / "sampling-stops.jsonl", {"optimizer_step": step,
                    "completed_optimizer_steps": self.state.global_step,
                    "eligible_questions": len(selected), "sampled_groups": len(attempts),
                    "reason": "fewer_than_two_effective_questions_within_scan_budget"})
                raise InsufficientEffectiveGroups("sampling budget did not yield two distinct effective questions")
            reward.selected = selected
            cached = ([ids for group in selected for ids in group["generated"][0]],
                      [ids for group in selected for ids in group["generated"][1]], None, {})
            self.cached_generation = cached
            selected_inputs = [copy.copy(group["input"]) for group in selected for _ in range(4)]
            for group in selected:
                verify_prompt_tokens(self.processing_class, group["input"], group["generated"][0],
                                     self.tools, self.chat_template_kwargs)
            result = super()._generate_and_score_completions(selected_inputs)
            actual_prompts = [ids[mask.bool()].tolist() for ids, mask in zip(result["prompt_ids"], result["prompt_mask"])]
            actual_completions = [ids[mask.bool()].tolist() for ids, mask in zip(result["completion_ids"], result["completion_mask"])]
            if actual_prompts != cached[0] or actual_completions != cached[1]:
                raise ValueError("TRL training tensors differ from the cached sampled tokens")
            reward.batches[-1]["cached_prompt_tokens_match"] = True
            reward.batches[-1]["processor_forward_prompt_tokens_match"] = True
            append_jsonl(reward.output / "effective-batches.jsonl", reward.batches[-1])
            return result

        def _generate_single_turn(self, prompts):
            cached = self.cached_generation
            self.cached_generation = None
            if cached is None or len(prompts) != 8:
                raise ValueError("the training pass must consume exactly the two cached G4 groups")
            return cached

    return GroupedTrainer


def verify_prompt_tokens(processor, sample_input, sampled_prompts, tools=None, template_kwargs=None):
    from trl.data_utils import apply_chat_template, prepare_multimodal_messages

    prompt = prepare_multimodal_messages(sample_input["prompt"], sample_input["images"])
    text = apply_chat_template({"prompt": prompt}, processor, tools=tools, **(template_kwargs or {}))["prompt"]
    options = {"images": [sample_input["images"]]} if sample_input["images"] else {}
    encoded = processor(text=[text], padding=True, return_tensors="pt", **options)
    tokens = encoded["input_ids"][0][encoded["attention_mask"][0].bool()].tolist()
    if any(tokens != sampled for sampled in sampled_prompts):
        raise ValueError("generation and the TRL forward processor produce different prompt tokens")


def grouped_monitor(output, reward, reference_sha256):
    from transformers import TrainerCallback

    class GroupedMonitor(TrainerCallback):
        def __init__(self):
            self.gradients, self.updates = [], []
            self.previous_actor = reference_sha256

        def on_pre_optimizer_step(self, args, state, control, model, **kwargs):
            import torch

            gradients = [parameter.grad.detach() for name, parameter in model.named_parameters()
                         if ".default." in name and parameter.grad is not None]
            row = {"optimizer_step": state.global_step + 1,
                "finite": all(bool(torch.isfinite(value).all()) for value in gradients),
                "l2": math.sqrt(sum(value.float().square().sum().item() for value in gradients))}
            self.gradients.append(row)
            append_jsonl(output / "gradient-steps.jsonl", row)
            if not row["finite"] or row["l2"] == 0:
                raise ValueError("selected effective groups must produce finite nonzero adapter gradients")

        def on_step_end(self, args, state, control, model, **kwargs):
            actor = adapter_summary(model, "default")["sha256"]
            reference = adapter_summary(model, "ref")["sha256"]
            if reference != reference_sha256 or actor == self.previous_actor:
                raise ValueError("an update must change the actor and preserve the reference")
            row = {**reward.batches[-1], "optimizer_step": state.global_step,
                "actor_changed": True, "reference_unchanged": True, "actor_sha256": actor,
                "gradient_l2": self.gradients[-1]["l2"]}
            self.updates.append(row)
            append_jsonl(output / "update-steps.jsonl", row)
            self.previous_actor = actor

        def on_save(self, args, state, control, **kwargs):
            checkpoint = output / f"checkpoint-{state.global_step}"
            write_json(checkpoint / "ready.json", {"optimizer_step": state.global_step,
                "actor_sha256": self.previous_actor,
                "adapter_file_sha256": file_hash(checkpoint / "adapter_model.safetensors")})

    return GroupedMonitor()


def run_grouped(output, samples, config, settings, training, plan):
    import torch
    from peft import PeftModel
    from transformers import AutoProcessor, Qwen3VLForConditionalGeneration, set_seed

    started = time.monotonic()
    torch.set_num_threads(4)
    set_seed(settings["seed"])
    processor = AutoProcessor.from_pretrained(ROOT / config["model_path"], local_files_only=True,
        min_pixels=256 * 28 * 28, max_pixels=config["max_pixels"])
    base = Qwen3VLForConditionalGeneration.from_pretrained(ROOT / config["model_path"],
        local_files_only=True, dtype=torch.bfloat16, attn_implementation="sdpa")
    base.config.use_cache = False
    model = PeftModel.from_pretrained(base, ROOT / config["adapter_path"], is_trainable=True)
    before = adapter_summary(model, "default")
    write_json(output / "default-before.json", before)
    reward = GroupedFactsReward(samples, output, {processor.tokenizer.eos_token_id, processor.tokenizer.pad_token_id})
    pool = training_dataset(samples)
    monitor = grouped_monitor(output, reward, before["sha256"])
    trainer = grouped_trainer(reward, pool, settings["max_generation_attempts"], settings["max_samples_per_update"])(
        model=model, args=training, processing_class=processor, train_dataset=pool,
        reward_funcs=reward, callbacks=[monitor])
    if trainer.accelerator.num_processes != 1:
        raise ValueError("this grouped trial runs on one GPU")
    reference = adapter_summary(model, "ref")
    if reference["sha256"] != before["sha256"] or any(parameter.requires_grad
            for name, parameter in model.named_parameters() if ".visual." in name or ".ref." in name):
        raise ValueError("reference must match the initial SFT adapter; reference and vision remain frozen")
    write_json(output / "reference-before.json", reference)
    stop_reason, metrics = None, {}
    try:
        metrics = trainer.train().metrics
    except InsufficientEffectiveGroups as error:
        stop_reason = str(error)
    after, reference_after = adapter_summary(model, "default"), adapter_summary(model, "ref")
    preserved = all(adapter_files(Path(path)) == hashes for path, hashes in plan["preserved_adapters"].items())
    write_json(output / "default-after.json", after)
    write_json(output / "reference-after.json", reference_after)
    if not preserved or reference_after["sha256"] != before["sha256"]:
        raise ValueError("the preserved adapters or reference changed")
    if trainer.state.global_step:
        model.save_pretrained(output / "adapter", selected_adapters=["default"])
        trainer.save_state()
    completion = {"completed": trainer.state.global_step == settings["max_steps"],
        "optimizer_steps": trainer.state.global_step, "stop_reason": stop_reason,
        "effective_groups": len(reward.groups), "updates": monitor.updates,
        "preserved_v4_v5_adapters_unchanged": preserved, "reference_unchanged": True,
        "actor_changed": after["sha256"] != before["sha256"], "metrics": metrics,
        "elapsed_seconds": time.monotonic() - started,
        "peak_allocated_mib": torch.cuda.max_memory_allocated() / 2**20}
    write_json(output / "completion.json", completion)
    print(json.dumps(completion, ensure_ascii=False, indent=2), flush=True)


def append_jsonl(path, value):
    with path.open("a") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")


def save_sources(output, sources):
    for relative in sources:
        target = output / "source-snapshot" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)


if __name__ == "__main__":
    main()
