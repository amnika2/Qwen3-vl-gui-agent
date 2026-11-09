"""Evaluate review prompts on training-application screenshots with one fixed adapter."""

import argparse
import copy
import hashlib
import importlib.metadata
import json
import time
from collections import Counter
from pathlib import Path

import yaml
from PIL import Image

from guiqa.cli import load_config
from guiqa.control import review_guard as old_guard
from guiqa.model import Qwen
from guiqa.review_contract import json_schema, parse_review as new_parse, review_guard as new_guard
from guiqa.review_evidence import parse_review as old_parse


def main():
    args = arguments()
    rows, config, manifest = load_inputs(args)
    output = args.output.resolve()
    predictions = prepare_output(output, manifest, args.resume)
    completed = {row["id"] for row in predictions}
    model = Qwen(config)
    model.load()
    for row in rows:
        if row["id"] in completed:
            continue
        result = evaluate_row(model, row, args.dataset.resolve(), manifest["adapter_sha256"])
        with (output / "predictions.jsonl").open("a") as handle:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
        predictions.append(result)
        write_summary(output, predictions, len(rows), model.metrics())
        print(json.dumps({"completed": len(predictions), "total": len(rows), "id": result["id"],
                          "label": result["label"], "prediction": result["prediction"],
                          "parse_error": result["parse_error"], "guard": result["guard"]}), flush=True)
    summary = write_summary(output, predictions, len(rows), model.metrics())
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--partition", choices=("train", "validation"), default="validation")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def load_inputs(args):
    root = Path(__file__).resolve().parents[1]
    dataset = args.dataset.resolve()
    split_path = dataset / f"{args.partition}.jsonl"
    rows = [json.loads(line) for line in split_path.read_text().splitlines() if line.strip()]
    rows = [row for row in rows if row["phase"] == "review"]
    split = yaml.safe_load((root / "configs/sft-split.yaml").read_text())
    training = set(split["training_pilot"] + split["training_supplementary"])
    if not rows or any(row["case_id"] not in training for row in rows):
        raise ValueError("validation requires review rows from the ten training applications")
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("review sample ids must be unique")
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError("limit must be positive")
        rows = rows[:args.limit]
    for row in rows:
        if row["review_format"] not in ("evidence_v1", "evidence_v2"):
            raise ValueError("only evidence_v1 and evidence_v2 review rows are supported")
        steps = {frame["step"] for frame in row["review_frames"]}
        parse = new_parse if row["review_format"] == "evidence_v2" else old_parse
        parse(row["messages"][-1]["content"], steps, row["review_profile"])
    original = [json.loads(line) for line in
                (root / "datasets/sft-main-10apps-v3/validation.jsonl").read_text().splitlines()]
    matched = {identifier for row in original if row["phase"] == "review"
               for identifier in (row["id"], row["derived_provenance"]["source_sample_id"])}
    for row in rows:
        source_id = row["derived_provenance"].get("source_sample_id")
        curriculum = row.get("sample_kind") == "review_curriculum" or row["derived_provenance"].get("curriculum")
        row["validation_cohort"] = ("new_curriculum" if curriculum else
                                    "matched_validation" if source_id in matched else "original_training")
    config = load_config(args.config.resolve())
    manifest = {
        "dataset": str(dataset), "partition": args.partition, "split_sha256": file_hash(split_path),
        "config": config, "config_sha256": file_hash(args.config.resolve()),
        "adapter_sha256": file_hash(Path(config["adapter_path"]) / "adapter_model.safetensors"),
        "sample_ids": [row["id"] for row in rows], "samples": len(rows),
        "generation": {"remaining_seconds": 180, "evidence_v1_max_tokens": 768,
                       "evidence_v2_max_tokens": 1024, "retries": 0},
        "lm_format_enforcer": importlib.metadata.version("lm-format-enforcer"),
        "code_sha256": {name: file_hash(root / name) for name in (
            "scripts/validate_review_protocol.py", "src/guiqa/model.py", "src/guiqa/review_contract.py",
            "src/guiqa/review_context.py", "src/guiqa/review_evidence.py", "src/guiqa/control.py")},
    }
    return rows, config, manifest


def prepare_output(output, manifest, resume):
    if resume:
        previous = json.loads((output / "manifest.json").read_text())
        if previous != manifest:
            raise ValueError("resume inputs differ from the saved validation manifest")
        path = output / "predictions.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        if len({row["id"] for row in rows}) != len(rows) or any(row["id"] not in manifest["sample_ids"] for row in rows):
            raise ValueError("saved predictions contain duplicate or unknown samples")
        return rows
    output.mkdir(parents=True, exist_ok=False)
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return []


def evaluate_row(model, row, dataset, adapter_sha256):
    messages = copy.deepcopy(row["messages"][:-1])
    prompt_sha256 = hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    images = []
    for message in messages:
        for item in message["content"]:
            if item["type"] == "image":
                path = dataset / item["image"]
                images.append({"path": str(path), "sha256": file_hash(path)})
                item["image"] = Image.open(path).convert("RGB")
    steps = {frame["step"] for frame in row["review_frames"]}
    new = row["review_format"] == "evidence_v2"
    options = {"max_new_tokens": 1024 if new else 768}
    if new:
        options["schema"] = json_schema(sorted(steps))
    start = time.monotonic()
    raw = model._generate(messages, remaining=180, **options)
    elapsed = time.monotonic() - start
    answer, guard, parse_error = None, None, None
    try:
        answer = (new_parse if new else old_parse)(raw, steps, row["review_profile"])
        guard = (new_guard if new else old_guard)(answer, row["review_profile"])
    except (ValueError, KeyError, TypeError) as error:
        parse_error = str(error)
    label = json.loads(row["messages"][-1]["content"])
    predicted = answer["verdict"] if answer is not None and guard is None and parse_error is None else "blocked"
    return {"id": row["id"], "case_id": row["case_id"], "review_format": row["review_format"],
            "source_run_id": row["derived_provenance"]["source_run_id"],
            "source_sample_id": row["derived_provenance"].get("source_sample_id"),
            "cohort": row["validation_cohort"], "label": label["verdict"], "prediction": predicted,
            "model_verdict": answer["verdict"] if answer else None, "guard": guard, "parse_error": parse_error,
            "raw": raw, "elapsed_seconds": elapsed,
            "field_readings": answer["field_readings"] if answer else None,
            "field_exact_matches": field_matches(label["field_readings"], answer["field_readings"] if answer else []),
            "source_image_hashes": images, "adapter_sha256": adapter_sha256, "prompt_sha256": prompt_sha256}


def field_matches(expected, predicted):
    found = {reading["field"].strip().casefold(): reading for reading in predicted}
    results = []
    for reading in expected:
        candidate = found.get(reading["field"].strip().casefold(), {})
        for name in ("before", "after") if "before" in reading else ("input", "observed"):
            truth = reading[name] if name in ("before", "after") else (
                {"value": reading[f"{name}_value"], "step": reading[f"{name}_step"]}
                if reading[f"{name}_value"] is not None else None)
            actual = candidate.get(name) if name in ("before", "after") else (
                {"value": candidate.get(f"{name}_value"), "step": candidate.get(f"{name}_step")})
            if truth is not None:
                results.append({"field": reading["field"], "stage": name, "expected": truth,
                                "predicted": actual, "value_exact": actual is not None and actual["value"] == truth["value"],
                                "step_exact": actual is not None and actual["step"] == truth["step"]})
    return results


def metrics(rows):
    values = [item for row in rows for item in row["field_exact_matches"]]
    correct = sum(row["prediction"] == row["label"] for row in rows)
    return {"samples": len(rows), "correct": correct, "accuracy": correct / len(rows) if rows else None,
            "format_errors": sum(row["parse_error"] is not None for row in rows),
            "guarded": sum(row["guard"] is not None for row in rows),
            "false_pass": sum(row["label"] == "fail" and row["prediction"] == "pass" for row in rows),
            "false_fail": sum(row["label"] == "pass" and row["prediction"] == "fail" for row in rows),
            "pass_on_blocked_label": sum(row["label"] == "blocked" and row["prediction"] == "pass" for row in rows),
            "field_value_exact": {"correct": sum(item["value_exact"] for item in values), "total": len(values)},
            "confusion": dict(Counter(f"{row['label']}->{row['prediction']}" for row in rows))}


def write_summary(output, rows, planned, model_metrics):
    summary = {"completed": len(rows) == planned, "planned": planned, "metrics": metrics(rows),
               "cohorts": {name: metrics([row for row in rows if row["cohort"] == name])
                           for name in sorted({row["cohort"] for row in rows})},
               "cases": {case: metrics([row for row in rows if row["case_id"] == case])
                         for case in sorted({row["case_id"] for row in rows})},
               "model": model_metrics,
               "metric_definitions": {"false_pass": "fail label predicted pass", "false_fail": "pass label predicted fail",
                                      "field_value_exact": "literal equality on non-null labelled readings; empty string is included"}}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    lines = [f"Fixed-adapter review validation: {len(rows)}/{planned} completed.", "",
             "| Cohort | Correct | Format errors | Guarded | False pass | False fail | Exact field values |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for name, value in [("all", summary["metrics"]), *summary["cohorts"].items()]:
        exact = value["field_value_exact"]
        lines.append(f"| {name} | {value['correct']}/{value['samples']} | {value['format_errors']} | "
                     f"{value['guarded']} | {value['false_pass']} | {value['false_fail']} | {exact['correct']}/{exact['total']} |")
    lines.extend(["", "Format errors and guarded predictions remain in the denominator as blocked.",
                  "These are review-only results on training applications, not end-to-end holdout scores."])
    (output / "summary.md").write_text("\n".join(lines) + "\n")
    return summary


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    main()
