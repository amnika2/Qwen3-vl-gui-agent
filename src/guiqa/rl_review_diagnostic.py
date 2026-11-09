"""Sample repeated v4 reviews on a frozen, training-only set of real evidence."""

import argparse
import copy
import hashlib
import importlib.metadata
import json
import time
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from .cli import load_config
from .review_contract import json_schema


ROOT = Path(__file__).resolve().parents[2]


def main():
    args = arguments()
    if args.mode == "prepare":
        result = prepare(args)
    elif args.mode == "worker":
        result = worker(args)
    else:
        result = merge(args)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


def prepare(args):
    from .rl_review_reward import validate_gold

    dataset = args.dataset.resolve()
    training = read_rows(dataset / "train.jsonl")
    validation = read_rows(dataset / "validation.jsonl")
    split_path = ROOT / "configs/sft-split.yaml"
    split = yaml.safe_load(split_path.read_text())
    rows = validate_training_reviews(training, validation, split)
    selected = select_reviews(rows, args.samples, args.seed)
    sources = []
    for row in selected:
        steps = {frame["step"] for frame in row["review_frames"]}
        gold = validate_gold(gold_answer(row), steps, row["review_profile"])
        images = image_sources(row, dataset)
        if not 1 <= len(images) <= 5:
            raise ValueError(f"review must contain between one and five images: {row['id']}")
        sources.append({"id": row["id"], "case_id": row["case_id"], "label": gold["verdict"],
                        "mechanism": mechanism(gold), "prompt_sha256": prompt_hash(row),
                        "source_image_hashes": images})
    config_path = args.config.resolve()
    config = load_config(config_path)
    code_paths = [ROOT / name for name in (
        "src/guiqa/rl_review_diagnostic.py", "src/guiqa/rl_review_reward.py",
        "src/guiqa/model.py", "src/guiqa/review_contract.py", "src/guiqa/cli.py")]
    manifest = {
        "version": 1, "dataset": str(dataset), "partition": "train", "config": config,
        "samples": len(selected), "candidates_per_sample": args.candidates,
        "selection": {"algorithm": "all_fail_then_round_robin_case_and_label",
                      "seed": args.seed, "available_reviews": len(rows),
                      "all_training_fail_included": True},
        "generation": {"seed": args.seed, "temperature": args.temperature, "top_p": args.top_p,
                       "top_k": 20, "repetition_penalty": 1.0,
                       "max_new_tokens": 1024, "do_sample": True,
                       "schema_constrained": True, "batch_size": args.batch_size,
                       "max_batch_prompt_tokens": args.max_batch_prompt_tokens, "retries": 0},
        "source_files": {str(path): file_hash(path) for path in (
            dataset / "train.jsonl", dataset / "validation.jsonl", split_path, config_path,
            Path(config["adapter_path"]) / "adapter_model.safetensors",
            Path(config["adapter_path"]) / "adapter_config.json",
            Path(config["model_path"]) / "generation_config.json",
            Path(config["model_path"]) / "config.json", *code_paths)},
        "adapter_sha256": file_hash(Path(config["adapter_path"]) / "adapter_model.safetensors"),
        "sample_sources": sources,
        "label_counts": dict(Counter(item["label"] for item in sources)),
        "case_counts": dict(Counter(item["case_id"] for item in sources)),
        "holdout_used": False, "development_used_for_training": False,
    }
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    write_rows(output / "selected.jsonl", selected)
    manifest["selected_sha256"] = file_hash(output / "selected.jsonl")
    write_json(output / "manifest.json", manifest)
    return {"output": str(output), "samples": len(selected),
            "candidates": len(selected) * args.candidates, "label_counts": manifest["label_counts"],
            "case_counts": manifest["case_counts"]}


def worker(args):
    from .model import Qwen
    from .rl_review_reward import score_review

    output = args.output.resolve()
    manifest, rows = load_run(output)
    assigned = [(index, row) for index, row in enumerate(rows) if index % args.shards == args.shard_index]
    predictions_path = output / f"shard-{args.shard_index}.jsonl"
    saved = read_rows(predictions_path) if predictions_path.exists() else []
    validate_saved(saved, rows, manifest, args.shard_index, args.shards)
    completed = {(row["id"], row["candidate_index"]) for row in saved}
    model = Qwen(manifest["config"])
    model.load()
    sources = {row["id"]: row for row in manifest["sample_sources"]}
    runtime_path = output / f"shard-{args.shard_index}-runtime.json"
    previous_runtime = json.loads(runtime_path.read_text()) if runtime_path.exists() else {}
    runtime = {"shard_index": args.shard_index, "shards": args.shards,
               "device": model.torch.cuda.get_device_name(0),
               "prompt_batches": previous_runtime.get("prompt_batches", {}),
               "versions": {name: importlib.metadata.version(name)
                            for name in ("torch", "transformers", "peft", "lm-format-enforcer")}}
    write_json(runtime_path, runtime)
    generated = 0
    for index, row in assigned:
        pending = [candidate for candidate in range(manifest["candidates_per_sample"])
                   if (row["id"], candidate) not in completed]
        if not pending:
            continue
        inputs, allowed_tokens = prepare_prompt(model, row, Path(manifest["dataset"]))
        steps = {frame["step"] for frame in row["review_frames"]}
        gold = gold_answer(row)
        prompt_tokens = inputs.input_ids.shape[1]
        batch_size = choose_batch_size(prompt_tokens, manifest["generation"])
        runtime["prompt_batches"][row["id"]] = {"prompt_tokens": prompt_tokens, "batch_size": batch_size}
        write_json(runtime_path, runtime)
        for offset in range(0, len(pending), batch_size):
            batch = pending[offset:offset + batch_size]
            seed = manifest["generation"]["seed"] + index * manifest["candidates_per_sample"] + batch[0]
            completions = generate_candidates(model, inputs, allowed_tokens, manifest["generation"], seed, len(batch))
            for candidate, (raw, sampling) in zip(batch, completions):
                score = score_review(raw, gold, steps, row["review_profile"])
                if sampling["truncated"]:
                    score.update(reward=None, strict_correct=False, transport_error="length_limit_without_eos")
                result = {**sources[row["id"]], "sample_index": index, "candidate_index": candidate,
                          "source_run_id": row["derived_provenance"]["source_run_id"],
                          "source_sample_id": row["derived_provenance"].get("source_sample_id"),
                          "adapter_sha256": manifest["adapter_sha256"], "raw": raw,
                          "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(), **sampling, **score}
                with predictions_path.open("a") as handle:
                    handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                    handle.flush()
                saved.append(result)
                generated += 1
                print(json.dumps({"shard": args.shard_index, "completed": len(saved),
                                  "planned": len(assigned) * manifest["candidates_per_sample"],
                                  "id": row["id"], "candidate": candidate, "label": result["label"],
                                  "prediction": result["prediction"], "reward": result["reward"],
                                  "strict_correct": result["strict_correct"],
                                  "batch_seconds": sampling["batch_elapsed_seconds"]}), flush=True)
            if args.max_candidates is not None and generated >= args.max_candidates:
                return {"shard": args.shard_index, "generated_this_run": generated,
                        "saved": len(saved), "stopped_after_requested_candidates": True}
    return {"shard": args.shard_index, "generated_this_run": generated, "saved": len(saved),
            "completed": len(saved) == len(assigned) * manifest["candidates_per_sample"]}


def merge(args):
    output = args.output.resolve()
    manifest, rows = load_run(output)
    predictions = []
    for index in range(args.shards):
        path = output / f"shard-{index}.jsonl"
        shard = read_rows(path) if path.exists() else []
        validate_saved(shard, rows, manifest, index, args.shards)
        predictions.extend(shard)
    predictions.sort(key=lambda row: (row["sample_index"], row["candidate_index"]))
    summary = summarize(predictions, manifest)
    write_rows(output / "predictions.jsonl", predictions)
    write_json(output / "summary.json", summary)
    write_json(output / "sample_diagnostics.json", sample_diagnostics(predictions, manifest))
    (output / "summary.md").write_text(summary_markdown(summary))
    return summary


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--dataset", type=Path, default=ROOT / "datasets/sft-main-10apps-v4")
    prep.add_argument("--config", type=Path, default=ROOT / "configs/agent-review-evidence-v4-selected.yaml")
    prep.add_argument("--samples", type=int, default=64)
    prep.add_argument("--candidates", type=int, default=8)
    prep.add_argument("--seed", type=int, default=42)
    prep.add_argument("--temperature", type=float, default=0.8)
    prep.add_argument("--top-p", type=float, default=0.95)
    prep.add_argument("--batch-size", type=int, choices=(1, 2, 4, 8), default=1)
    prep.add_argument("--max-batch-prompt-tokens", type=int, default=16000)
    work = commands.add_parser("worker")
    work.add_argument("--shard-index", type=int, required=True)
    work.add_argument("--shards", type=int, default=2)
    work.add_argument("--max-candidates", type=int)
    combine = commands.add_parser("merge")
    combine.add_argument("--shards", type=int, default=2)
    for command in (prep, work, combine):
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "prepare" and (args.samples < 1 or args.candidates < 2
                                    or args.temperature <= 0 or not 0 < args.top_p <= 1
                                    or args.max_batch_prompt_tokens < 1):
        parser.error("samples and prompt-token budget must be positive, candidates >= 2, temperature > 0 and 0 < top-p <= 1")
    if args.mode != "prepare" and args.shards < 1:
        parser.error("shards must be positive")
    if args.mode == "worker" and (not 0 <= args.shard_index < args.shards
                                   or args.max_candidates is not None and args.max_candidates < 1):
        parser.error("shard-index must identify a shard and max-candidates must be positive")
    return args


def validate_training_reviews(training, validation, split):
    rows = [row for row in training if row["phase"] == "review"]
    allowed = set(split["training_pilot"] + split["training_supplementary"])
    if not rows or len({row["id"] for row in rows}) != len(rows):
        raise ValueError("training reviews must be nonempty and have unique IDs")
    for row in rows:
        if row["case_id"] not in allowed or row["data_split"] != "train" or row["review_format"] != "evidence_v2":
            raise ValueError("diagnostic inputs must be evidence_v2 reviews from training applications and partition")
        if row["messages"][-1]["role"] != "assistant" or any(
                message["role"] == "assistant" for message in row["messages"][:-1]):
            raise ValueError("review prompts must contain no assistant answer")
    for key in ("scenario_id", "scenario_fingerprint"):
        train_values = {row[key] for row in rows}
        validation_values = {row[key] for row in validation}
        if train_values & validation_values:
            raise ValueError(f"training and validation overlap on {key}")
    train_runs = {row["derived_provenance"]["source_run_id"] for row in rows}
    validation_runs = {row["derived_provenance"]["source_run_id"] for row in validation
                       if row.get("derived_provenance")}
    if train_runs & validation_runs:
        raise ValueError("training and validation reuse the same source run")
    return rows


def select_reviews(rows, count, seed):
    failed = [row for row in rows if gold_answer(row)["verdict"] == "fail"]
    if not len(failed) <= count <= len(rows):
        raise ValueError("sample count must include all training fail reviews and not exceed available reviews")
    order = lambda row: hashlib.sha256(f"{seed}:{row['id']}".encode()).hexdigest()
    selected = sorted(failed, key=order)
    buckets = defaultdict(list)
    for row in rows:
        label = gold_answer(row)["verdict"]
        if label != "fail":
            buckets[(row["case_id"], label)].append(row)
    for bucket in buckets.values():
        bucket.sort(key=order)
    while len(selected) < count:
        for key in sorted(buckets):
            if buckets[key]:
                selected.append(buckets[key].pop(0))
                if len(selected) == count:
                    break
    return selected


def gold_answer(row):
    content = row["messages"][-1]["content"]
    return json.loads(content if isinstance(content, str) else content[0]["text"])


def mechanism(gold):
    if gold["expected_rejection"]:
        return "expected_business_rejection"
    if gold["submission"]["status"] == "rejected":
        return "business_rejection"
    if gold["verdict"] == "blocked":
        if any(field["intent"] == "edit" and field["input_matches_request"] is not True
               for field in gold["field_readings"]):
            return "input_not_established"
        if gold["submission"]["status"] != "accepted":
            return "submission_not_established"
        if not gold["result_observed"]:
            return "result_not_established"
        return "incomplete_field_evidence"
    if gold["verdict"] == "fail":
        return "accepted_persistence_mismatch"
    if gold["task_kind"] == "create":
        return "record_creation"
    if any(field["intent"] == "preserve" for field in gold["field_readings"]):
        return "saved_with_preservation"
    return "accepted_persistence_match"


def prepare_prompt(model, row, dataset):
    from PIL import Image
    from lmformatenforcer import JsonSchemaParser
    from lmformatenforcer.integrations.transformers import (
        build_token_enforcer_tokenizer_data, build_transformers_prefix_allowed_tokens_fn)

    messages = copy.deepcopy(row["messages"][:-1])
    for message in messages:
        for item in message["content"]:
            if item["type"] == "image":
                with Image.open(dataset / item["image"]) as image:
                    item["image"] = image.convert("RGB")
    inputs = model.processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                return_dict=True, return_tensors="pt").to(model.model.device)
    if model.review_tokenizer_data is None:
        model.review_tokenizer_data = build_token_enforcer_tokenizer_data(model.processor.tokenizer)
    steps = [frame["step"] for frame in row["review_frames"]]
    allowed_tokens = build_transformers_prefix_allowed_tokens_fn(
        model.review_tokenizer_data, JsonSchemaParser(json_schema(steps)))
    return inputs, allowed_tokens


def choose_batch_size(prompt_tokens, generation):
    return min(generation["batch_size"], max(1, generation["max_batch_prompt_tokens"] // prompt_tokens))


def generate_candidates(model, inputs, allowed_tokens, generation, seed, batch_size):
    model.torch.manual_seed(seed)
    model.torch.cuda.reset_peak_memory_stats(0)
    start = time.monotonic()
    with model.torch.inference_mode():
        outputs = model.model.generate(**inputs, do_sample=True, temperature=generation["temperature"],
                    top_p=generation["top_p"], top_k=generation["top_k"],
                    repetition_penalty=generation["repetition_penalty"],
                    max_new_tokens=generation["max_new_tokens"],
                    prefix_allowed_tokens_fn=allowed_tokens, use_cache=True,
                    num_return_sequences=batch_size)
    elapsed = time.monotonic() - start
    eos = model.model.generation_config.eos_token_id
    eos_ids = {eos} if isinstance(eos, int) else set(eos)
    results = []
    for sequence in outputs:
        completion = sequence[inputs.input_ids.shape[1]:].tolist()
        first_eos = next((index for index, token in enumerate(completion) if token in eos_ids), None)
        if first_eos is not None:
            completion = completion[:first_eos + 1]
        raw = model.processor.tokenizer.decode(completion, skip_special_tokens=True)
        results.append((raw, {"batch_seed": seed, "independent_candidate_seed": seed if batch_size == 1 else None,
                             "sampling_batch_size": batch_size, "prompt_tokens": inputs.input_ids.shape[1],
                             "completion_tokens": len(completion), "truncated": first_eos is None,
                             "batch_elapsed_seconds": elapsed, "elapsed_seconds": elapsed / batch_size,
                             "peak_allocated_mib": model.torch.cuda.max_memory_allocated(0) / 2**20}))
    return results


def load_run(output):
    manifest = json.loads((output / "manifest.json").read_text())
    for name, expected in manifest["source_files"].items():
        if file_hash(Path(name)) != expected:
            raise ValueError(f"frozen diagnostic input changed: {name}")
    if file_hash(output / "selected.jsonl") != manifest["selected_sha256"]:
        raise ValueError("frozen review selection changed")
    for source in manifest["sample_sources"]:
        for item in source["source_image_hashes"]:
            if file_hash(Path(item["path"])) != item["sha256"]:
                raise ValueError(f"source screenshot changed: {item['path']}")
    return manifest, read_rows(output / "selected.jsonl")


def validate_saved(saved, rows, manifest, index, shards):
    by_id = {row["id"]: position for position, row in enumerate(rows)}
    keys = [(row["id"], row["candidate_index"]) for row in saved]
    if len(set(keys)) != len(keys):
        raise ValueError("saved candidates contain duplicate identities")
    for row in saved:
        position = by_id.get(row["id"])
        if (position is None or position % shards != index or row["sample_index"] != position
                or not 0 <= row["candidate_index"] < manifest["candidates_per_sample"]
                or row["adapter_sha256"] != manifest["adapter_sha256"]
                or row["prompt_sha256"] != manifest["sample_sources"][position]["prompt_sha256"]):
            raise ValueError("saved candidate does not match its frozen source and shard")


def sample_diagnostics(predictions, manifest):
    grouped = defaultdict(list)
    for row in predictions:
        grouped[row["id"]].append(row)
    results = []
    for source in manifest["sample_sources"]:
        candidates = grouped[source["id"]]
        rewards = [row["reward"] for row in candidates if row["reward"] is not None]
        full = len(candidates) == manifest["candidates_per_sample"]
        reward_complete = full and len(rewards) == len(candidates)
        results.append({"id": source["id"], "case_id": source["case_id"], "label": source["label"],
                        "mechanism": source["mechanism"], "completed_candidates": len(candidates),
                        "fully_sampled": full,
                        "any_verdict_correct": any(row["verdict_correct"] for row in candidates),
                        "any_strict_correct": any(row["strict_correct"] for row in candidates),
                        "verdict_correct_candidates": sum(row["verdict_correct"] for row in candidates),
                        "strict_correct_candidates": sum(row["strict_correct"] for row in candidates),
                        "trusted_reward_candidates": len(rewards), "reward_complete": reward_complete,
                        "reward_range": max(rewards) - min(rewards) if rewards else None,
                        "zero_reward_variance": len(set(rewards)) == 1 if reward_complete else None,
                        "unique_completions": len({row["raw_sha256"] for row in candidates}),
                        "needs_review": sum(row["needs_review"] for row in candidates)})
    return results


def summarize(predictions, manifest):
    diagnostics = sample_diagnostics(predictions, manifest)
    groups = {"all": aggregate(predictions, diagnostics)}
    for field in ("label", "case_id", "mechanism"):
        groups[field] = {value: aggregate([row for row in predictions if row[field] == value],
                                         [row for row in diagnostics if row[field] == value])
                         for value in sorted({row[field] for row in diagnostics})}
    return {"completed": len(predictions) == manifest["samples"] * manifest["candidates_per_sample"],
            "planned_samples": manifest["samples"], "candidates_per_sample": manifest["candidates_per_sample"],
            "planned_candidates": manifest["samples"] * manifest["candidates_per_sample"],
            "training_only": True, "holdout_used": False, **groups,
            "definitions": {"any_verdict_correct": "at least one sampled answer has the gold verdict; guard and fact correctness are reported separately",
                            "any_strict_correct": "at least one sampled answer has the gold verdict and all independently verified core facts/citations",
                            "zero_reward_variance": "all candidates completed and all have trusted numeric rewards, which are equal",
                            "needs_review": "candidate uses an unverified alternative evidence citation; not counted as trusted training reward"}}


def aggregate(predictions, diagnostics):
    complete = [row for row in diagnostics if row["fully_sampled"]]
    trusted = [row for row in diagnostics if row["reward_complete"]]
    return {"samples": len(diagnostics), "fully_sampled": len(complete), "candidates": len(predictions),
            "verdict_correct_candidates": sum(row["verdict_correct"] for row in predictions),
            "strict_correct_candidates": sum(row["strict_correct"] for row in predictions),
            "candidate_verdict_accuracy": sum(row["verdict_correct"] for row in predictions) / len(predictions) if predictions else None,
            "candidate_strict_accuracy": sum(row["strict_correct"] for row in predictions) / len(predictions) if predictions else None,
            "any_verdict_correct": sum(row["any_verdict_correct"] for row in complete),
            "any_strict_correct": sum(row["any_strict_correct"] for row in complete),
            "pass_at_k_verdict": sum(row["any_verdict_correct"] for row in complete) / len(complete) if complete else None,
            "pass_at_k_strict": sum(row["any_strict_correct"] for row in complete) / len(complete) if complete else None,
            "trusted_reward_candidates": sum(row["reward"] is not None for row in predictions),
            "trusted_reward_coverage": sum(row["reward"] is not None for row in predictions) / len(predictions) if predictions else None,
            "trusted_complete_groups": len(trusted),
            "zero_reward_variance_groups": sum(row["zero_reward_variance"] for row in trusted),
            "zero_reward_variance_fraction": sum(row["zero_reward_variance"] for row in trusted) / len(trusted) if trusted else None,
            "needs_review_candidates": sum(row["needs_review"] for row in predictions),
            "format_errors": sum(row["parse_error"] is not None for row in predictions),
            "truncated": sum(row["truncated"] for row in predictions),
            "confusion": dict(Counter(f"{row['label']}->{row['prediction']}" for row in predictions)),
            "errors": dict(Counter(error for row in predictions for error in row["errors"]))}


def summary_markdown(summary):
    lines = [f"训练证据审查采样诊断：{summary['all']['candidates']}/{summary['planned_candidates']} 个候选。", "",
             "| 标签 | 完整题组 | 候选结论正确 | 至少一次结论正确 | 至少一次事实与引用正确 | 可信奖励候选 | 待复核引用 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, stats in [("all", summary["all"]), *summary["label"].items()]:
        lines.append(f"| {name} | {stats['fully_sampled']}/{stats['samples']} | "
                     f"{stats['verdict_correct_candidates']}/{stats['candidates']} | "
                     f"{stats['any_verdict_correct']}/{stats['fully_sampled']} | "
                     f"{stats['any_strict_correct']}/{stats['fully_sampled']} | "
                     f"{stats['trusted_reward_candidates']}/{stats['candidates']} | {stats['needs_review_candidates']} |")
    lines.extend(["", "同题多次采样命中率用于检查探索信号；单次回答能力需要另行评测。",
                  "替代截图引用未经独立核验时保留为待复审，不直接作为错误或可信训练奖励。"])
    return "\n".join(lines) + "\n"


def image_sources(row, dataset):
    return [{"path": str((dataset / item["image"]).resolve()), "sha256": file_hash(dataset / item["image"])}
            for message in row["messages"][:-1] for item in message["content"] if item["type"] == "image"]


def prompt_hash(row):
    return hashlib.sha256(json.dumps(row["messages"][:-1], ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def file_hash(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
