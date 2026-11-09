"""Run the selected model on explicitly annotated training-app review evidence."""

import argparse
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from guiqa.artifacts import write_json
from guiqa.cli import load_config
from guiqa.control import review_guard
from guiqa.model import Qwen
from guiqa.review_evidence import parse_review
from guiqa.sft_v2 import checksum, read_json, read_rows, trace_history


def main():
    args = arguments()
    root = Path(__file__).resolve().parents[1]
    dataset = (root / args.dataset).resolve()
    config = load_config(root / args.config)
    samples = load_samples(dataset, args.limit, config)
    model = Qwen(config)
    directory = root / (args.output or Path("runs") / (
        "review-evidence-smoke-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-" + uuid.uuid4().hex[:8]))
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "run.json", {"dataset": str(dataset), "config": config,
               "source_train_sha256": checksum(dataset / "train.jsonl"),
               "sample_ids": [sample["id"] for sample in samples]})
    model.begin_run()
    results = []
    for sample in samples:
        content = sample["messages"][1]["content"]
        provenance = sample["derived_provenance"]
        source = root / "runs" / provenance["source_run_id"]
        task = read_json(source / "result.json")["task"]
        history = trace_history(read_rows(source / "trace.jsonl"))[:provenance["history_end_step"] + 1]
        frames = sample["review_frames"]
        images = [dataset / item["image"] for item in content if item["type"] == "image"]
        screenshots = [(frame["step"], image) for frame, image in zip(frames, images)]
        profile = sample["review_profile"]
        start = time.monotonic()
        raw = model.assess(task, screenshots, history, config["wall_seconds"], profile=profile, frames=frames)
        row = {"sample_id": sample["id"], "case_id": sample["case_id"],
               "review_frames": frames, "elapsed_seconds": time.monotonic() - start,
               "raw": raw, "parsed": None, "parse_error": None}
        try:
            row["parsed"] = parse_review(raw, {frame["step"] for frame in frames}, profile)
        except (ValueError, KeyError, TypeError) as error:
            row["parse_error"] = f"{type(error).__name__}: {error}"
        expected = json.loads(sample["messages"][-1]["content"])
        row["annotated_verdict"] = expected["verdict"]
        row["annotated_field_readings"] = expected["field_readings"]
        row.update(read_values_match=False, verdict_matches=False, guard=None, final_verdict=None)
        if row["parsed"] is not None:
            parsed = row["parsed"]
            row["guard"] = review_guard(parsed, profile)
            row["final_verdict"] = "blocked" if row["guard"] else parsed["verdict"]
            reading_keys = ("field", "requested_value", "input_value", "observed_value")
            actual_values = [{key: reading[key] for key in reading_keys} for reading in parsed["field_readings"]]
            expected_values = [{key: reading[key] for key in reading_keys} for reading in expected["field_readings"]]
            for values in (actual_values, expected_values):
                for reading in values:
                    reading["field"] = reading["field"].casefold()
                values.sort(key=lambda reading: reading["field"])
            row["read_values_match"] = actual_values == expected_values
            row["verdict_matches"] = row["final_verdict"] == expected["verdict"]
        results.append(row)
        write_json(directory / "results.json", results)
        print(json.dumps({key: row[key] for key in ("sample_id", "elapsed_seconds", "parse_error",
                         "read_values_match", "final_verdict", "verdict_matches")}), flush=True)
    summary = {"completed": True, "run_dir": str(directory), "samples": len(results),
               "valid_schema": sum(row["parse_error"] is None for row in results),
               "read_values_match": sum(row["read_values_match"] for row in results),
               "verdict_matches": sum(row["verdict_matches"] for row in results), "gpu": model.metrics()}
    write_json(directory / "summary.json", summary)
    print(json.dumps(summary), flush=True)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("datasets/sft-review-evidence-preview-final"))
    parser.add_argument("--config", type=Path, default=Path("configs/agent-review-evidence.yaml"))
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    return args


def load_samples(dataset, limit, config):
    if config.get("review_format") != "evidence_v1":
        raise ValueError("the review smoke requires review_format: evidence_v1")
    manifest = read_json(dataset / "manifest.json")
    samples = read_rows(dataset / "train.jsonl")[:limit]
    if not samples:
        raise ValueError("the dataset has no training reviews")
    if any(sample["review_format"] != "evidence_v1" or sample["case_id"] in manifest["eval_holdout"]
           for sample in samples):
        raise ValueError("only evidence_v1 reviews from training applications are supported")
    return samples


if __name__ == "__main__":
    main()
