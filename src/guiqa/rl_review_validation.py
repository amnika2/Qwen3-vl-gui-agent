"""Greedy review evaluation on independent development or final-test evidence."""

import argparse
import copy
import hashlib
import importlib.metadata
import json
import time
from collections import Counter
from pathlib import Path

import yaml

from .cli import load_config
from .rl_review_diagnostic import file_hash, gold_answer, image_sources, prompt_hash, read_rows, write_json
from .rl_review_reward import score_review, validate_gold
from .rl_reward_rules import load_reward_rules
from .review_contract import MAX_OBSERVATION_LENGTH, json_schema


ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--dataset", type=Path, default=ROOT / "datasets/rl-review-pilot-v2")
    parser.add_argument("--split", choices=("validation", "test"), default="validation")
    parser.add_argument("--config", type=Path, default=ROOT / "configs/agent-review-evidence-v4-selected.yaml")
    parser.add_argument("--rules", type=Path, default=ROOT / "configs/rl-review-reward-rules.yaml")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-samples", type=int)
    parser.add_argument("--max-new-tokens", type=int, default=1024)
    parser.add_argument("--schema-constrained", action="store_true")
    parser.add_argument("--facts-only", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--merge", action="store_true")
    args = parser.parse_args()
    if args.merge:
        result = merge_validation(args.output, args.shards)
    else:
        if args.adapter is None:
            parser.error("--adapter is required for generation")
        result = evaluate_adapter(args.adapter, args.dataset, args.output, args.config, args.rules,
                                  args.max_samples, args.max_new_tokens, args.check_only,
                                  args.shard_index, args.shards, args.schema_constrained, args.facts_only,
                                  args.split)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


def evaluate_adapter(adapter_path, dataset_root, output, config_path=None, rules_path=None,
                     max_samples=None, max_new_tokens=1024, check_only=False, shard_index=0, shards=1,
                     schema_constrained=False, facts_only=False, partition="validation"):
    adapter, dataset, output = Path(adapter_path).resolve(), Path(dataset_root).resolve(), Path(output).resolve()
    config_path = Path(config_path or ROOT / "configs/agent-review-evidence-v4-selected.yaml").resolve()
    rules_path = Path(rules_path or ROOT / "configs/rl-review-reward-rules.yaml").resolve()
    full_rows = validation_rows(dataset, max_samples, partition)
    if facts_only:
        full_rows = facts_rows(full_rows)
        schema_constrained = True
    if shards < 1 or not 0 <= shard_index < shards:
        raise ValueError("shard-index must identify a shard")
    rows = [row for index, row in enumerate(full_rows) if index % shards == shard_index]
    global_indices = {row["id"]: index for index, row in enumerate(full_rows)}
    if shards > 1:
        output = output / f"shard-{shard_index}"
    config = load_config(config_path)
    config["adapter_path"] = str(adapter)
    if facts_only:
        config["review_format"] = "evidence_facts_v1"
    rules = load_reward_rules(rules_path)
    manifest = evaluation_manifest(full_rows, adapter, dataset, config, config_path, rules_path, max_new_tokens,
                                   schema_constrained, facts_only, partition)
    manifest["sharding"] = {"index": shard_index, "shards": shards, "assigned_ids": [row["id"] for row in rows]}
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"
    if manifest_path.exists():
        if json.loads(manifest_path.read_text()) != manifest:
            raise ValueError("saved development evaluation has different inputs or generation settings")
    else:
        write_json(manifest_path, manifest)
    if check_only:
        return {"mode": "cpu_preflight", "samples": len(rows), "label_counts": manifest["label_counts"],
                "output": str(output), "schema_constrained": schema_constrained, "model_loaded": False}
    predictions_path = output / "predictions.jsonl"
    predictions = read_rows(predictions_path) if predictions_path.exists() else []
    if [row["id"] for row in predictions] != [row["id"] for row in rows[:len(predictions)]]:
        raise ValueError("saved development answers do not match the selected sample order")
    if len(predictions) < len(rows):
        from .model import Qwen

        model = Qwen(config)
        model.begin_run()
        with predictions_path.open("a") as handle:
            for index, row in enumerate(rows[len(predictions):], start=len(predictions)):
                raw, generation = generate_greedy(model, row, dataset, max_new_tokens, schema_constrained)
                gold = gold_answer(row)
                sample_rules = {"field_aliases": rules["case_aliases"].get(row["case_id"], {})}
                score, rendered_raw = score_completion(
                    raw, gold, {frame["step"] for frame in row["review_frames"]}, row["review_profile"],
                    sample_rules, generation["truncated"], facts_only)
                verdict = terminal_verdict(raw)
                prediction = {"id": row["id"], "sample_index": global_indices[row["id"]], "case_id": row["case_id"],
                              "source_run_id": row["derived_provenance"]["source_run_id"],
                              "label": gold["verdict"], "raw": raw,
                              "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                              "terminal_verdict": verdict,
                              "terminal_verdict_correct": verdict == gold["verdict"],
                              **generation, **score}
                if facts_only:
                    prediction["rendered_raw"] = rendered_raw
                handle.write(json.dumps(prediction, ensure_ascii=False) + "\n")
                handle.flush()
                predictions.append(prediction)
                write_json(output / "summary.json", summarize_validation(predictions, len(rows)))
                print(json.dumps({"output": str(output), "completed": len(predictions), "samples": len(rows),
                                  "label": gold["verdict"], "terminal_verdict": verdict,
                                  "strict_correct": score["strict_correct"],
                                  "elapsed_seconds": generation["elapsed_seconds"]}), flush=True)
        write_json(output / "runtime.json", model.metrics())
    summary = summarize_validation(predictions, len(rows))
    write_json(output / "summary.json", summary)
    return summary


def merge_validation(output, shards):
    output = Path(output).resolve()
    manifests, predictions = [], []
    for index in range(shards):
        directory = output / f"shard-{index}" if shards > 1 else output
        manifest = json.loads((directory / "manifest.json").read_text())
        sharding = manifest.pop("sharding")
        if sharding["index"] != index or sharding["shards"] != shards:
            raise ValueError("development shard identity differs")
        manifests.append(manifest)
        rows = read_rows(directory / "predictions.jsonl")
        if [row["id"] for row in rows] != sharding["assigned_ids"][:len(rows)]:
            raise ValueError("development shard answer order differs")
        predictions.extend(rows)
    if any(manifest != manifests[0] for manifest in manifests[1:]):
        raise ValueError("development shards use different adapters, inputs, or generation settings")
    predictions.sort(key=lambda row: row["sample_index"])
    expected_ids = [row["id"] for row in manifests[0]["sample_sources"]]
    if (len({row["id"] for row in predictions}) != len(predictions)
            or any(row["id"] != expected_ids[row["sample_index"]] for row in predictions)):
        raise ValueError("development predictions do not match their frozen sample indices")
    summary = summarize_validation(predictions, len(expected_ids))
    if shards > 1:
        write_json(output / "manifest.json", {**manifests[0], "merged_shards": shards})
        with (output / "predictions.jsonl").open("w") as handle:
            for row in predictions:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    write_json(output / "summary.json", summary)
    return summary


def validation_rows(dataset, max_samples=None, partition="validation"):
    if partition not in {"validation", "test"}:
        raise ValueError("review evaluation split must be validation or test")
    source = dataset / f"{partition}-evidence-v2.jsonl"
    if not source.exists():
        source = dataset / f"{partition}.jsonl"
    training = read_rows(dataset / "train.jsonl") if partition == "validation" else []
    validation = read_rows(source)
    split = yaml.safe_load((ROOT / "configs/sft-split.yaml").read_text())
    allowed = set(split["eval_holdout"] if partition == "test"
                  else split["training_pilot"] + split["training_supplementary"])
    if not validation or len({row["id"] for row in validation}) != len(validation):
        raise ValueError("development reviews require nonempty unique sample IDs")
    for row in validation:
        if (row["data_split"] != partition or row["phase"] != "review"
                or row["review_format"] != "evidence_v2" or row["case_id"] not in allowed):
            raise ValueError(f"{partition} reviews must come from their frozen application partition")
        if row["messages"][-1]["role"] != "assistant" or any(
                message["role"] == "assistant" for message in row["messages"][:-1]):
            raise ValueError("development prompts must contain no assistant answer")
        validate_gold(gold_answer(row), {frame["step"] for frame in row["review_frames"]}, row["review_profile"])
    for key in ("scenario_id", "scenario_fingerprint"):
        if {row[key] for row in training} & {row[key] for row in validation}:
            raise ValueError("development and training overlap on " + key)
    if ({row["derived_provenance"]["source_run_id"] for row in training}
            & {row["derived_provenance"]["source_run_id"] for row in validation}):
        raise ValueError("development and training share source runs")
    if max_samples is None:
        return validation
    if not 1 <= max_samples <= len(validation):
        raise ValueError("max_samples must be within the development dataset size")
    by_label = {label: [row for row in validation if gold_answer(row)["verdict"] == label]
                for label in ("fail", "pass", "blocked")}
    selected = []
    while len(selected) < max_samples:
        for label in by_label:
            if by_label[label] and len(selected) < max_samples:
                selected.append(by_label[label].pop(0))
    return selected


def evaluation_manifest(rows, adapter, dataset, config, config_path, rules_path, max_new_tokens,
                        schema_constrained=False, facts_only=False, partition="validation"):
    if max_new_tokens < 1:
        raise ValueError("max_new_tokens must be positive")
    source = dataset / f"{partition}-evidence-v2.jsonl"
    if not source.exists():
        source = dataset / f"{partition}.jsonl"
    data_files = [source]
    if partition == "validation":
        data_files.append(dataset / "train.jsonl")
    manifest = {"version": 1, "dataset": str(dataset), "partition": partition, "samples": len(rows),
            "config": config, "adapter": str(adapter),
            "adapter_sha256": {name: file_hash(adapter / name)
                               for name in ("adapter_config.json", "adapter_model.safetensors")},
            "dataset_sha256": {path.name: file_hash(path) for path in data_files},
            "config_sha256": file_hash(config_path), "rules_sha256": file_hash(rules_path),
            "source_sha256": {name: file_hash(ROOT / name) for name in (
                "src/guiqa/rl_review_validation.py", "src/guiqa/rl_review_reward.py", "src/guiqa/model.py",
                "src/guiqa/review_contract.py", "src/guiqa/rl_reward_rules.py")},
            "generation": {"do_sample": False, "schema_constrained": schema_constrained,
                           "max_new_tokens": max_new_tokens, "batch_size": 1, "seed": config["seed"]},
            "label_counts": dict(Counter(gold_answer(row)["verdict"] for row in rows)),
            "sample_sources": [{"id": row["id"], "prompt_sha256": prompt_hash(row),
                                "images": image_sources(row, dataset)} for row in rows],
            "run_specific_reward_rules_applied": False, "case_aliases_applied": True,
            "holdout_used": partition == "test", "validation_used_to_fit_rules": False,
            "metric_note": "Strict scores require the original annotated citations; unseen equivalent development citations remain unverified. Free-text explanation correctness is not scored."}
    if schema_constrained:
        manifest["generation"].update(
            max_observation_characters=MAX_OBSERVATION_LENGTH,
            lm_format_enforcer=importlib.metadata.version("lm-format-enforcer"),
            schema="guiqa.review_contract.json_schema(sorted(review_frame_steps))")
    if facts_only:
        manifest["source_sha256"]["src/guiqa/review_facts.py"] = file_hash(ROOT / "src/guiqa/review_facts.py")
        manifest["generation"].pop("max_observation_characters")
        manifest["generation"].update(
            schema="guiqa.review_facts.facts_schema(sorted(review_frame_steps))",
            model_generated_explanations=False)
        manifest["prompt_transform"] = {
            "review_format": "evidence_facts_v1", "system_prompt": "guiqa.review_facts.facts_prompt(profile)",
            "user_and_images_unchanged": True,
            "original_prompt_sha256": {row["id"]: row["original_prompt_sha256"] for row in rows},
            "explanation_renderer": "guiqa.review_facts.render_facts", "gold_transform": "guiqa.review_facts.to_facts"}
    return manifest


def facts_rows(rows):
    from .review_facts import facts_prompt, to_facts

    converted = copy.deepcopy(rows)
    for original, row in zip(rows, converted):
        row["original_prompt_sha256"] = prompt_hash(original)
        row["review_format"] = "evidence_facts_v1"
        row["messages"][0]["content"] = [{"type": "text", "text": facts_prompt(row["review_profile"])}]
        row["messages"][-1]["content"] = json.dumps(to_facts(gold_answer(original)), ensure_ascii=False)
    return converted


def score_completion(raw, gold, steps, profile, rules, truncated, facts_only):
    rendered_raw = None
    if facts_only:
        from .review_facts import parse_facts, render_facts

        gold = render_facts(gold)
        try:
            rendered_raw = json.dumps(parse_facts(raw, steps, profile), ensure_ascii=False)
        except (ValueError, KeyError, TypeError) as error:
            score = score_review("{}", gold, steps, profile, rules=rules, truncated=truncated)
            score["parse_error"] = str(error)
            return score, None
    return score_review(rendered_raw if facts_only else raw, gold, steps, profile,
                        rules=rules, truncated=truncated), rendered_raw


def generate_greedy(model, row, dataset, max_new_tokens, schema_constrained=False):
    from PIL import Image

    messages = copy.deepcopy(row["messages"][:-1])
    for message in messages:
        for item in message["content"]:
            if item["type"] == "image":
                with Image.open(dataset / item["image"]) as screenshot:
                    item["image"] = screenshot.convert("RGB")
    inputs = model.processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                                  return_dict=True, return_tensors="pt").to(model.model.device)
    options = {}
    if schema_constrained:
        from lmformatenforcer import JsonSchemaParser
        from lmformatenforcer.integrations.transformers import (
            build_token_enforcer_tokenizer_data, build_transformers_prefix_allowed_tokens_fn)

        if model.review_tokenizer_data is None:
            model.review_tokenizer_data = build_token_enforcer_tokenizer_data(model.processor.tokenizer)
        steps = sorted({frame["step"] for frame in row["review_frames"]})
        schema = json_schema(steps)
        if row["review_format"] == "evidence_facts_v1":
            from .review_facts import facts_schema

            schema = facts_schema(steps)
        options["prefix_allowed_tokens_fn"] = build_transformers_prefix_allowed_tokens_fn(
            model.review_tokenizer_data, JsonSchemaParser(schema))
    started = time.monotonic()
    with model.torch.inference_mode():
        generated = model.model.generate(**inputs, do_sample=False, max_new_tokens=max_new_tokens,
                                         use_cache=True, **options)
    ids = generated[0, inputs.input_ids.shape[1]:].tolist()
    raw = model.processor.tokenizer.decode(ids, skip_special_tokens=True)
    end_tokens = {model.processor.tokenizer.eos_token_id, model.processor.tokenizer.pad_token_id}
    return raw, {"prompt_tokens": inputs.input_ids.shape[1], "completion_tokens": len(ids),
                 "truncated": ids[-1] not in end_tokens, "elapsed_seconds": time.monotonic() - started}


def terminal_verdict(raw):
    try:
        answer = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return answer.get("verdict") if isinstance(answer, dict) and answer.get("verdict") in {
        "pass", "fail", "blocked"} else None


def summarize_validation(rows, planned):
    return {"completed": len(rows) == planned, "planned_samples": planned,
            "all": validation_metrics(rows),
            "label": {label: validation_metrics([row for row in rows if row["label"] == label])
                      for label in ("pass", "fail", "blocked")},
            "case_id": {case: validation_metrics([row for row in rows if row["case_id"] == case])
                        for case in sorted({row["case_id"] for row in rows})}}


def validation_metrics(rows):
    count = len(rows)
    normal = [row for row in rows if row["label"] == "pass"]
    defects = [row for row in rows if row["label"] == "fail"]
    numeric = [row["reward"] for row in rows if row["reward"] is not None]
    classes = {}
    for label in ("pass", "fail", "blocked"):
        true_positive = sum(row["label"] == label and row["terminal_verdict"] == label for row in rows)
        predicted = sum(row["terminal_verdict"] == label for row in rows)
        actual = sum(row["label"] == label for row in rows)
        precision = true_positive / predicted if predicted else 0.0
        recall = true_positive / actual if actual else 0.0
        classes[label] = {"precision": precision, "recall": recall,
                          "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0}
    detected = sum(row["terminal_verdict"] == "fail" for row in defects)
    false_alarms = sum(row["terminal_verdict"] == "fail" for row in normal)
    false_passes = sum(row["terminal_verdict"] == "pass" for row in defects)
    return {"samples": count, "terminal_verdict_correct": sum(row["terminal_verdict_correct"] for row in rows),
            "reported_guarded_correct": sum(
                not row["truncated"] and row["parse_error"] is None
                and ("blocked" if row["guard"] is not None else row["terminal_verdict"]) == row["label"]
                for row in rows),
            "terminal_verdict_accuracy": sum(row["terminal_verdict_correct"] for row in rows) / count if count else None,
            "macro_f1": sum(metrics["f1"] for metrics in classes.values()) / 3,
            "class_metrics": classes,
            "strict_correct": sum(row["strict_correct"] for row in rows),
            "strict_accuracy": sum(row["strict_correct"] for row in rows) / count if count else None,
            "format_errors": sum(row["parse_error"] is not None for row in rows),
            "guard_errors": sum(row["guard"] is not None for row in rows),
            "truncated": sum(row["truncated"] for row in rows),
            "blocked_predictions": sum(row["terminal_verdict"] == "blocked" for row in rows),
            "normal_samples": len(normal),
            "normal_false_alarms": false_alarms,
            "normal_false_alarm_rate": false_alarms / len(normal) if normal else None,
            "defect_samples": len(defects),
            "defects_detected": detected,
            "defect_recall": detected / len(defects) if defects else None,
            "defects_false_passed": false_passes,
            "defect_false_pass_rate": false_passes / len(defects) if defects else None,
            "numeric_rewards": len(numeric), "mean_numeric_reward": sum(numeric) / len(numeric) if numeric else None,
            "confusion": dict(Counter(row["label"] + "->" + str(row["terminal_verdict"]) for row in rows))}


if __name__ == "__main__":
    main()
