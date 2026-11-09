"""Review frozen v3 holdout trajectories without replaying or changing their operations."""

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from guiqa.artifacts import write_json
from guiqa.cli import load_config
from guiqa.control import LoopControl
from guiqa.model import Qwen
from guiqa.review_context import select_evidence
from guiqa.review_contract import parse_review, review_guard
from guiqa.sft_v2 import checksum, read_json, read_rows, trace_history, value_hash


CASES = ("005", "007", "008", "040", "041")
CODE_FILES = (
    "scripts/review_sft_v4_holdout.py", "src/guiqa/model.py", "src/guiqa/review_context.py",
    "src/guiqa/review_contract.py", "src/guiqa/review_evidence.py", "src/guiqa/control.py",
    "src/guiqa/actions.py", "src/guiqa/sft_v2.py",
)


def main():
    args = arguments()
    root = Path(__file__).resolve().parents[1]
    config, sources, manifest = load_inputs(root, args)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "manifest.json", manifest)
    model = Qwen(config)
    model.load()
    predictions = []
    for source in sources:
        result = review_source(root, model, source)
        with (output / "results.jsonl").open("a") as handle:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
        predictions.append(result)
        write_json(output / "summary.json", shard_summary(predictions, len(sources)))
        print(json.dumps({"run_id": result["source_run_id"], "completed": len(predictions),
                          "planned": len(sources), "source_group": result["source_group"],
                          "new_verdict": result["new_verdict"], "error_kind": result["error_kind"]}), flush=True)
    write_json(output / "model.json", model.metrics())
    print(json.dumps(shard_summary(predictions, len(sources)), ensure_ascii=False, indent=2), flush=True)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shards", type=int, default=4)
    return parser.parse_args()


def load_inputs(root, args):
    if args.shards < 1 or not 0 <= args.shard_index < args.shards:
        raise ValueError("shard-index must be between zero and shards minus one")
    if not args.output.resolve().is_relative_to(root / "reports"):
        raise ValueError("holdout review output must be under reports")
    config = load_config(args.config.resolve())
    if config.get("review_format") != "evidence_v2":
        raise ValueError("holdout re-review requires an evidence_v2 config")
    adapter = Path(config["adapter_path"])
    completion_path = adapter.parent / "completion.json"
    completion = read_json(completion_path)
    adapter_hash = checksum(adapter / "adapter_model.safetensors")
    if not completion["completed"] or adapter_hash != completion["adapter_sha256"]:
        raise ValueError("adapter must match a completed training selection")
    cells, completed_files = [], {}
    for case in CASES:
        path = root / f"runs/sft-lora-v3-v3-sweeper-{case}/completed.json"
        completed_files[str(path.relative_to(root))] = checksum(path)
        cells.extend(read_json(path))
    if len(cells) != 40 or len({cell["run_id"] for cell in cells}) != 40:
        raise ValueError("source evaluation must contain forty distinct completed v3 trajectories")
    sources = [prepare_source(root, cell) for cell in cells]
    selected = [source for index, source in enumerate(sources) if index % args.shards == args.shard_index]
    frozen = [{key: source[key] for key in
               ("source_run_id", "case_id", "source_hashes", "screenshot_hashes", "review_frames",
                "source_agent_verdict", "source_oracle_verdict", "matrix_expected", "source_group")}
              for source in sources]
    manifest = {
        "purpose": "holdout_review_only", "training_rows_generated": 0, "operations_replayed": False,
        "checkpoint_selection": "completed training selection; holdout reviews are not checkpoint selection inputs",
        "config_path": str(args.config.resolve()), "config_sha256": checksum(args.config.resolve()), "config": config,
        "adapter_sha256": adapter_hash, "training_completion_path": str(completion_path),
        "training_completion_sha256": checksum(completion_path), "selected_epoch": completion["selected_epoch"],
        "source_completed_files": completed_files, "source_data_sha256": value_hash(frozen),
        "source_runs": frozen, "source_group_counts": dict(Counter(source["source_group"] for source in sources)),
        "source_total": len(sources), "shard_index": args.shard_index, "shards": args.shards,
        "planned_run_ids": [source["source_run_id"] for source in selected],
        "source_code_sha256": {name: checksum(root / name) for name in CODE_FILES},
        "generation": {"remaining_seconds": 180, "review_max_new_tokens": config.get("review_max_new_tokens"), "retries": 0},
    }
    return config, selected, manifest


def prepare_source(root, cell):
    directory = root / cell["path"]
    result = read_json(directory / "result.json")
    if result["oracle_verdict"] not in ("pass", "fail") or result["oracle_verdict"] != result["matrix_expected"]:
        raise ValueError(f"{cell['run_id']}: source oracle must match its expected outcome without infrastructure errors")
    if result["run_id"] != cell["run_id"] or result["case_id"] != cell["case_id"]:
        raise ValueError("completed source identity differs from its run artifact")
    before, after = result["intervention_step"], result["actions"]
    history = trace_history([row for row in read_rows(directory / "trace.jsonl") if row["step"] <= after])
    control = LoopControl(directory / "screenshots/000.png")
    for row in history:
        if 0 < row["step"] <= before:
            control.record(row["action"], row["step"], directory / f"screenshots/{row['step']:03d}.png")
    events = read_rows(directory / "model.jsonl")
    finishes = [event["action"] for event in events if event.get("step") == before
                and event.get("action", {}).get("action") == "finish" and event.get("phase") is None]
    nominations = finishes[-1]["evidence_steps"] if finishes else []
    frames = select_evidence(history, control.differences, before, after, nominations)
    source_hashes = {str((directory / name).relative_to(root)): checksum(directory / name)
                     for name in ("result.json", "trace.jsonl", "model.jsonl")}
    screenshot_hashes = {str((directory / f"screenshots/{frame['step']:03d}.png").relative_to(root)):
                         checksum(directory / f"screenshots/{frame['step']:03d}.png") for frame in frames}
    original = result["agent_verdict"]
    group = "original_blocked" if original == "blocked" else "original_correct"
    return {
        "source_run_id": result["run_id"], "case_id": result["case_id"], "source_path": cell["path"],
        "source_agent_verdict": original, "source_oracle_verdict": result["oracle_verdict"],
        "matrix_expected": result["matrix_expected"], "source_group": group,
        "source_error_kind": error_kind(original, result["oracle_verdict"],
                                         result["observation"] if result["termination_reason"] == "review_invalid_output" else None,
                                         result.get("review_guard")),
        "source_hashes": source_hashes, "screenshot_hashes": screenshot_hashes,
        "task": result["task"], "profile": result.get("review_profile", "persistence"),
        "history": history, "review_frames": frames, "nominated_steps": nominations,
        "before": before, "after": after, "last_type_step": control.last_type_step,
    }


def review_source(root, model, source):
    screenshots = [(frame["step"], root / source["source_path"] / f"screenshots/{frame['step']:03d}.png")
                   for frame in source["review_frames"]]
    start = time.monotonic()
    raw = model.assess(source["task"], screenshots, source["history"], remaining=180,
                       profile=source["profile"], frames=source["review_frames"])
    elapsed = time.monotonic() - start
    answer, guard, parse_error = None, None, None
    try:
        answer = parse_review(raw, {step for step, _ in screenshots}, source["profile"])
        guard = review_guard(answer, source["profile"])
        if not guard and answer["verdict"] != "blocked" and source["last_type_step"] == source["before"]:
            guard = "last_edit_not_submitted"
    except (ValueError, KeyError, TypeError) as error:
        parse_error = str(error)
    verdict = answer["verdict"] if answer is not None and guard is None and parse_error is None else "blocked"
    expected = source["source_oracle_verdict"]
    unchanged = all(checksum(root / name) == sha for name, sha in
                    {**source["source_hashes"], **source["screenshot_hashes"]}.items())
    return {
        **{key: source[key] for key in ("source_run_id", "case_id", "source_path", "source_agent_verdict",
                                      "source_oracle_verdict", "matrix_expected", "source_group", "source_error_kind",
                                      "source_hashes", "screenshot_hashes", "review_frames", "nominated_steps")},
        "before": source["before"], "after": source["after"], "operation_actions": source["before"],
        "operations_replayed": False, "actions_unchanged": unchanged, "source_artifacts_unchanged": unchanged,
        "raw": raw, "parsed": answer, "model_verdict": answer["verdict"] if answer else None,
        "new_verdict": verdict, "guard": guard, "parse_error": parse_error, "elapsed_seconds": elapsed,
        "correct": verdict == expected, "recovered": source["source_group"] == "original_blocked" and verdict == expected,
        "regressed": source["source_group"] == "original_correct" and verdict != expected,
        "error_kind": error_kind(verdict, expected, parse_error, guard),
    }


def error_kind(verdict, expected, parse_error, guard):
    if parse_error is not None:
        return "schema_parse_failure"
    if guard is not None:
        return "semantic_guard"
    if verdict == "blocked":
        return "model_blocked"
    return "correct" if verdict == expected else "business_result_misjudgment"


def shard_summary(rows, planned):
    return {
        "completed": len(rows) == planned, "planned": planned, "reviewed": len(rows),
        "correct": sum(row["correct"] for row in rows), "recovered_original_blocked": sum(row["recovered"] for row in rows),
        "regressed_original_correct": sum(row["regressed"] for row in rows),
        "error_kinds": dict(Counter(row["error_kind"] for row in rows)),
        "source_group_counts": dict(Counter(row["source_group"] for row in rows)),
        "source_artifacts_unchanged": all(row["source_artifacts_unchanged"] for row in rows),
        "operations_replayed": False, "training_rows_generated": 0,
    }


if __name__ == "__main__":
    main()
