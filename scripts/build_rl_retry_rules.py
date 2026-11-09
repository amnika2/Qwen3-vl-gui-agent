"""Bind verified retry rules to the fixed 32-question training curriculum."""

import argparse
import copy
import hashlib
import json
from pathlib import Path

import yaml

from guiqa.rl_review_diagnostic import gold_answer, read_rows
from guiqa.rl_review_reward import score_review
from guiqa.rl_reward_rules import load_reward_rules, rules_for_sample
from guiqa.rl_review_retry import binary_training_reward, group_eligible
from guiqa.train_review_grpo_pilot import select_rows


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "configs/rl-review-reward-rules-retry.yaml")
    args = parser.parse_args()
    directory = ROOT / "reports/rl-review-retry-calibration-v1"
    base = ROOT / "configs/rl-review-reward-rules.yaml"
    patches = [directory / name for name in ("php_oc_rules.yaml", "other_rules.yaml")]
    settings = yaml.safe_load((ROOT / "configs/rl-review-grpo-pilot.yaml").read_text())
    dataset = ROOT / settings["dataset"]
    selected = select_rows(read_rows(dataset / "train.jsonl"), settings["source_run_ids"])
    catalog = copy.deepcopy(load_reward_rules(base))
    for path in patches:
        for run_id, patch in yaml.safe_load(path.read_text())["samples"].items():
            previous = catalog["samples"].get(run_id, {})
            merged = {**previous, **patch}
            for key in ("field_aliases", "approved_citations", "invalid_citations",
                        "approved_requested_values", "approved_values"):
                values = {**previous.get(key, {}), **patch.get(key, {})}
                if key != "field_aliases":
                    for name in previous.get(key, {}).keys() & patch.get(key, {}).keys():
                        values[name] = list(dict.fromkeys(previous[key][name] + patch[key][name]))
                merged[key] = values
            catalog["samples"][run_id] = merged
    for row in selected:
        run_id = row["derived_provenance"]["source_run_id"]
        if run_id not in catalog["samples"]:
            images = [dataset / item["image"] for message in row["messages"][:-1]
                      for item in message["content"] if item["type"] == "image"]
            catalog["samples"][run_id] = {
                "case_id": row["case_id"], "sample_id": row["id"], "field_aliases": {},
                "evidence_images": {frame["step"]: digest(image)
                                    for frame, image in zip(row["review_frames"], images)},
            }
    args.output.write_text(yaml.safe_dump(catalog, allow_unicode=True, sort_keys=False))
    verified = load_reward_rules(args.output)
    bound = {row["id"]: rules_for_sample(verified, row, dataset) for row in selected}
    sources = {row["id"]: row for row in selected}
    old = read_rows(ROOT / "runs/rl-review-grpo-pilot-v2/rollouts.jsonl")
    rescored = []
    for row in old:
        source = sources[row["sample_id"]]
        score = score_review(row["raw"], gold_answer(source),
                             {frame["step"] for frame in source["review_frames"]},
                             source["review_profile"], rules=bound[source["id"]],
                             truncated=row["score"].get("transport_error") == "length_limit_without_eos")
        rescored.append({**row, "old_score": row["score"], "score": score,
                         "label": gold_answer(source)["verdict"]})
    with (directory / "rescored_rollouts.jsonl").open("w") as handle:
        for row in rescored:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    report = {"rules": str(args.output), "rules_sha256": digest(args.output),
              "sources": {str(path): digest(path) for path in [base, *patches]},
              "selected_samples_bound": len(bound), "training_only": True,
              "before": counts(old, "score"), "after": counts(rescored, "score"),
              "strict_lost": sum(row["old_score"]["strict_correct"] and not row["score"]["strict_correct"]
                                 for row in rescored)}
    groups = {}
    for row in rescored:
        groups.setdefault(row["group"], []).append(row)
    eligible = [group for group in groups.values()
                if group_eligible([binary_training_reward(row["score"]) for row in group])]
    report["binary_eligible_groups"] = {
        label: sum(group[0]["label"] == label for group in eligible)
        for label in ("pass", "fail", "blocked")}
    report["binary_eligible_samples"] = {
        label: len({group[0]["sample_id"] for group in eligible if group[0]["label"] == label})
        for label in ("pass", "fail", "blocked")}
    retry = copy.deepcopy(settings)
    retry.update(reward_rules=str(args.output.relative_to(ROOT)), max_steps=10, save_steps=5,
                 task_reward="binary_verified", max_generation_attempts=2, shuffle_once=False)
    priority = [
        "typesetter-birch-A-saved", "20261107T173548Z-3df9e242", "typesetter-hazel-B-saved",
        "20261107T161416Z-47cd0702", "opencart-birch-B-cancelled", "phpipam-hazel-B-saved",
        "typesetter-hazel-A-saved", "phpipam-birch-A-input_not_established",
        "wolf-birch-edit-B-saved", "20261107T160018Z-d8f0a8b8",
    ]
    retry["source_run_ids"] = priority + [run for run in settings["source_run_ids"] if run not in priority]
    retry_path = ROOT / "configs/rl-review-grpo-retry.yaml"
    retry_path.write_text(yaml.safe_dump(retry, allow_unicode=True, sort_keys=False))
    report["retry_config_sha256"] = digest(retry_path)
    (directory / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def counts(rows, key):
    scores = [row[key] for row in rows]
    return {"candidates": len(scores), "numeric": sum(score["reward"] is not None for score in scores),
            "strict": sum(score["strict_correct"] for score in scores),
            "unscored": sum(score["reward"] is None for score in scores)}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    main()
