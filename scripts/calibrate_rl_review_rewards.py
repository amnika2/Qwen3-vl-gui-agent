"""Rescore saved training reviews with verified reward rules on CPU."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from guiqa import rl_review_reward
from guiqa.rl_review_diagnostic import (
    file_hash,
    gold_answer,
    prompt_hash,
    read_rows,
    summarize,
    validate_saved,
    write_json,
)
from guiqa.rl_reward_rules import load_reward_rules, rules_for_sample


ROOT = Path(__file__).resolve().parents[1]


def main():
    args = arguments()
    manifest, selected, predictions, baseline, provenance = load_inputs(args.input, args.rules)
    catalog = load_reward_rules(args.rules)
    dataset = Path(manifest["dataset"])
    sources = {
        row["id"]: (gold_answer(row), {frame["step"] for frame in row["review_frames"]},
                    row["review_profile"], rules_for_sample(catalog, row, dataset))
        for row in selected
    }
    previous = {(row["id"], row["candidate_index"]): row for row in baseline}
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / "manifest.json", provenance)
    rescored, comparisons = [], []
    with (args.output / "predictions.jsonl").open("w") as answers, (
        args.output / "comparison.jsonl"
    ).open("w") as changes:
        for row in predictions:
            gold, steps, profile, rules = sources[row["id"]]
            score = rl_review_reward.score_review(row["raw"], gold, steps, profile, rules=rules,
                                                   truncated=row["truncated"])
            calibrated = {**row, **score, "calibrated": True,
                          "reward_rules_sha256": provenance["rules_sha256"]}
            comparison = compare_candidate(previous[(row["id"], row["candidate_index"])], calibrated)
            answers.write(json.dumps(calibrated, ensure_ascii=False) + "\n")
            answers.flush()
            changes.write(json.dumps(comparison, ensure_ascii=False) + "\n")
            changes.flush()
            rescored.append(calibrated)
            comparisons.append(comparison)
    summary = summarize(rescored, manifest)
    summary.update(provenance=provenance, reward_counts=grouped_reward_counts(rescored))
    summary["definitions"]["reward_counts"] = "Counts use reward only; unresolved and truncated answers retain null."
    summary["definitions"]["needs_review"] = "Some evidence or fields remain unresolved; an independent verified error can still establish a negative reward."
    comparison = summarize_comparison(baseline, rescored, comparisons)
    legacy = legacy_training_replay(predictions)
    training_comparison = summarize_comparison(
        legacy, rescored, [compare_candidate(before, after) for before, after in zip(legacy, rescored)])
    training_comparison["definitions"]["baseline"] = (
        "Original unadjudicated scores with the old training resolver's four certain-error rules. "
        "The original worker masked five truncated parse failures, while the old training callback scored them -1.")
    comparison["legacy_training_policy_replay"] = training_comparison
    comparison["provenance"] = provenance
    write_json(args.output / "summary.json", summary)
    write_json(args.output / "comparison.json", comparison)
    print(json.dumps({"output": str(args.output), "before": comparison["before"]["all"],
                      "old_training_policy": training_comparison["before"]["all"],
                      "after": comparison["after"]["all"], "changes": comparison["changes"]},
                     ensure_ascii=False, indent=2))


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path,
                        default=ROOT / "reports/rl-review-diagnostic-v1-distributed")
    parser.add_argument("--rules", type=Path, default=ROOT / "configs/rl-review-reward-rules.yaml")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/rl-review-reward-calibration-v1")
    args = parser.parse_args()
    args.input, args.rules, args.output = args.input.resolve(), args.rules.resolve(), args.output.resolve()
    return args


def load_inputs(directory, rules_path):
    manifest_path = directory / "manifest.json"
    selected_path = directory / "selected.jsonl"
    predictions_path = directory / "predictions.jsonl"
    baseline_path = directory / "adjudicated_predictions.jsonl"
    manifest = json.loads(manifest_path.read_text())
    reward_path = Path(rl_review_reward.__file__).resolve()
    original_reward_hash = manifest["source_files"][str(reward_path)]
    source_verification = {}
    for name, expected in manifest["source_files"].items():
        actual = file_hash(Path(name))
        intentional_update = Path(name).resolve() == reward_path
        if actual != expected and not intentional_update:
            raise ValueError("frozen diagnostic input changed: " + name)
        source_verification[name] = {"original_sha256": expected, "current_sha256": actual,
                                     "reward_update_allowed": intentional_update}
    if file_hash(selected_path) != manifest["selected_sha256"]:
        raise ValueError("frozen review selection changed")
    for source in manifest["sample_sources"]:
        for image in source["source_image_hashes"]:
            if file_hash(Path(image["path"])) != image["sha256"]:
                raise ValueError("source screenshot changed: " + image["path"])
    selected = read_rows(selected_path)
    if manifest["partition"] != "train" or any(row["data_split"] != "train" for row in selected):
        raise ValueError("reward calibration requires the frozen training partition")
    for row, source in zip(selected, manifest["sample_sources"]):
        if row["id"] != source["id"] or prompt_hash(row) != source["prompt_sha256"]:
            raise ValueError("frozen prompt changed: " + row["id"])
    predictions, baseline = read_rows(predictions_path), read_rows(baseline_path)
    validate_saved(predictions, selected, manifest, 0, 1)
    validate_saved(baseline, selected, manifest, 0, 1)
    expected_count = manifest["samples"] * manifest["candidates_per_sample"]
    if len(predictions) != expected_count or len(baseline) != expected_count:
        raise ValueError("calibration requires every original and adjudicated candidate")
    original = {(row["id"], row["candidate_index"]): row for row in predictions}
    for row in baseline:
        source = original[(row["id"], row["candidate_index"])]
        raw_hash = hashlib.sha256(source["raw"].encode()).hexdigest()
        if raw_hash != source["raw_sha256"] or row["raw"] != source["raw"] or row["raw_sha256"] != raw_hash:
            raise ValueError("saved raw answer changed: " + row["id"])
    provenance = {
        "version": 1,
        "input_directory": str(directory),
        "dataset": manifest["dataset"],
        "source_manifest_sha256": file_hash(manifest_path),
        "selected_sha256": file_hash(selected_path),
        "raw_predictions_sha256": file_hash(predictions_path),
        "adjudicated_predictions_sha256": file_hash(baseline_path),
        "reward_source": str(reward_path),
        "original_reward_sha256": original_reward_hash,
        "calibrated_reward_sha256": file_hash(reward_path),
        "rules_path": str(rules_path),
        "rules_sha256": file_hash(rules_path),
        "rules_loader_sha256": file_hash(ROOT / "src/guiqa/rl_reward_rules.py"),
        "calibration_script_sha256": file_hash(Path(__file__)),
        "source_files_verification": source_verification,
        "training_only": True,
        "new_generation": False,
        "gpu_used": False,
        "raw_answers_preserved": True,
    }
    return manifest, selected, predictions, baseline, provenance


def reward_counts(rows):
    numeric = [row["reward"] for row in rows if row["reward"] is not None]
    return {
        "candidates": len(rows),
        "numeric_rewards": len(numeric),
        "none_rewards": len(rows) - len(numeric),
        "strict_correct_candidates": sum(row["strict_correct"] for row in rows),
        "positive_rewards": sum(reward > 0 for reward in numeric),
        "confirmed_negative_rewards": sum(reward < 0 for reward in numeric),
        "zero_rewards": sum(reward == 0 for reward in numeric),
        "truncated": sum(row["truncated"] for row in rows),
    }


def legacy_training_replay(rows):
    certain_errors = {"verdict:mismatch", "expected_rejection:mismatch",
                      "guard:review_rejection_verdict_contradicts_task",
                      "guard:review_result_verdict_contradicts_fields"}
    results = []
    for row in rows:
        reward = row["reward"]
        # The diagnostic worker masked these parse failures; the old training callback did not.
        if row["truncated"] and row["parse_error"] is not None:
            reward = -1.0
        elif reward is None and certain_errors.intersection(row["errors"]):
            reward = -1.0
        results.append({**row, "reward": reward})
    return results


def grouped_reward_counts(rows):
    return {
        "all": reward_counts(rows),
        "label": {label: reward_counts([row for row in rows if row["label"] == label])
                  for label in ("pass", "fail", "blocked")},
    }


def compare_candidate(previous, current):
    fields = ("reward", "strict_correct", "core_facts_correct", "needs_review", "parse_error", "guard",
              "errors", "unverified_citations", "unverified_fields")
    return {
        "id": current["id"], "source_run_id": current["source_run_id"],
        "case_id": current["case_id"], "sample_index": current["sample_index"],
        "candidate_index": current["candidate_index"], "label": current["label"],
        "raw_sha256": current["raw_sha256"], "truncated": current["truncated"],
        "before": {field: previous[field] for field in fields},
        "after": {field: current[field] for field in fields},
        "reward_changed": previous["reward"] != current["reward"],
        "strict_changed": previous["strict_correct"] != current["strict_correct"],
    }


def summarize_comparison(previous, current, comparisons):
    changes = Counter()
    transitions = Counter()
    for row in comparisons:
        before, after = row["before"], row["after"]
        changes["reward_changed"] += row["reward_changed"]
        changes["strict_gained"] += not before["strict_correct"] and after["strict_correct"]
        changes["strict_lost"] += before["strict_correct"] and not after["strict_correct"]
        changes["none_to_numeric"] += before["reward"] is None and after["reward"] is not None
        changes["numeric_to_none"] += before["reward"] is not None and after["reward"] is None
        changes["none_to_positive"] += before["reward"] is None and after["reward"] is not None and after["reward"] > 0
        changes["none_to_negative"] += before["reward"] is None and after["reward"] is not None and after["reward"] < 0
        transitions[reward_status(before["reward"]) + "->" + reward_status(after["reward"])] += 1
    gained = [row for row in comparisons if not row["before"]["strict_correct"] and row["after"]["strict_correct"]]
    return {
        "before": grouped_reward_counts(previous),
        "after": grouped_reward_counts(current),
        "changes": dict(changes),
        "reward_status_transitions": dict(sorted(transitions.items())),
        "new_strict_candidates": [
            {key: row[key] for key in ("id", "source_run_id", "case_id", "sample_index", "candidate_index", "label")}
            for row in gained
        ],
        "definitions": {
            "baseline": "Saved adjudicated_predictions.jsonl under the original reward code.",
            "confirmed_negative_rewards": "Candidates with a numeric reward below zero; provisional_reward is never substituted.",
            "truncated": "The saved length-limit marker always keeps reward=null and strict_correct=false.",
        },
    }


def reward_status(reward):
    if reward is None:
        return "none"
    if reward > 0:
        return "positive"
    return "negative" if reward < 0 else "zero"


if __name__ == "__main__":
    main()
