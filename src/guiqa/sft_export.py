import argparse
import collections
import copy
import hashlib
import json
import shutil
from pathlib import Path

import yaml

from guiqa.actions import parse_action
from guiqa.control import review_guard
from guiqa.model import OPERATION_PROMPTS, REVIEW_PROMPTS


def main():
    parser = argparse.ArgumentParser(description="Export observed SFT samples from explicit training batches")
    parser.add_argument("--experiment", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bundle-images", action="store_true")
    parser.add_argument("--deduplicate-trajectories", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    result = export(root, args.experiment, args.output, bundle_images=args.bundle_images,
                    deduplicate_trajectories=args.deduplicate_trajectories)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def export(root, experiments, output, bundle_images=False, deduplicate_trajectories=False):
    split = yaml.safe_load((root / "configs/sft-split.yaml").read_text())
    pilot = split.get("training_pilot", [])
    supplementary = split.get("training_supplementary", [])
    pool = split.get("training_pool", [])
    allowed = set(pilot + supplementary + pool) - set(split.get("deferred", {})) - set(split["eval_holdout"])
    source_of = {case: "supplementary" for case in supplementary}
    batches = [(root / "runs" / name) for name in experiments]
    samples, accepted, excluded = [], [], []
    fingerprints = set()
    trajectory_fingerprints = set()
    for directory in batches:
        for cell in json.loads((directory / "completed.json").read_text()):
            run = root / cell["path"]
            result = json.loads((run / "result.json").read_text())
            if result["case_id"] not in allowed:
                raise ValueError(f"{run.name}: case outside active training split: {result['case_id']}")
            if result["method"] != "sft" or result["oracle_verdict"] not in ("pass", "fail"):
                excluded.append({"run_id": result["run_id"], "reason": result["oracle_verdict"]})
                continue
            fingerprint = result.get("scenario_fingerprint")
            if fingerprint and not cell["matrix_matches"]:
                excluded.append({"run_id": result["run_id"], "reason": "scenario_did_not_match_calibration"})
                continue
            if fingerprint and fingerprint in fingerprints:
                excluded.append({"run_id": result["run_id"], "reason": "duplicate_scenario"})
                continue
            data_split = result.get("data_split", "train")
            if data_split not in {"train", "validation"}:
                raise ValueError(f"{run.name}: unknown dataset split {data_split}")
            source = run / "sft/samples.jsonl"
            rows = [json.loads(line) for line in source.read_text().splitlines()]
            review = rows[-1]
            if review["phase"] != "review" or review.get("label_source") != "observed_run_v2":
                raise ValueError(f"{run.name}: review is not labeled from this execution")
            if review["assistant"]["verdict"] != result["agent_verdict"]:
                raise ValueError(f"{run.name}: review and run verdict disagree")
            if review["assistant"]["verdict"] in ("pass", "fail") and review["assistant"]["verdict"] != result["oracle_verdict"]:
                raise ValueError(f"{run.name}: review contradicts observed business outcome")
            profile = result.get("review_profile", "persistence")
            guard = review_guard(review["assistant"], profile=profile)
            if guard:
                raise ValueError(f"{run.name}: review contradicts prompt profile: {guard}")
            if profile != "persistence":
                for row in rows:
                    expected_prompt = REVIEW_PROMPTS[profile] if row["phase"] == "review" else OPERATION_PROMPTS[profile]
                    if row["messages"][0]["content"][0]["text"] != expected_prompt:
                        raise ValueError(f"{run.name}: recorded prompt does not match {profile}")
            trajectory_fingerprint = hashlib.sha256(json.dumps({
                "case_id": result["case_id"], "task": result["task"], "actual": result["actual"],
                "verdict": result["oracle_verdict"],
                "actions": [{key: value for key, value in row["assistant"].items() if key != "coordinate"}
                            for row in rows if row["phase"] == "operation"]
            }, ensure_ascii=False, sort_keys=True).encode()).hexdigest() if deduplicate_trajectories else None
            if trajectory_fingerprint in trajectory_fingerprints:
                excluded.append({"run_id": result["run_id"], "reason": "duplicate_trajectory"})
                continue
            for index, row in enumerate(rows):
                parse_action(json.dumps(row["assistant"]), set(range(result["actions"] + 1)))
                messages = copy.deepcopy(row["messages"]) + [{"role": "assistant", "content": json.dumps(row["assistant"], ensure_ascii=False)}]
                sample = {"id": f"{run.name}:{index}", "case_id": result["case_id"], "workflow": result["workflow"],
                          "phase": row["phase"], "messages": messages}
                if fingerprint:
                    sample.update(scenario_id=result["scenario_id"], scenario_family=result["scenario_family"],
                                  data_split=data_split, case_source=source_of.get(result["case_id"], "official"))
                samples.append(sample)
            accepted.append({"run_id": result["run_id"], "case_id": result["case_id"], "build": result["build"],
                             "workflow": result["workflow"], "samples": len(rows),
                             "source": source_of.get(result["case_id"], "official"),
                             "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
            if profile != "persistence":
                accepted[-1]["review_profile"] = profile
            if trajectory_fingerprint:
                trajectory_fingerprints.add(trajectory_fingerprint)
                accepted[-1]["trajectory_fingerprint"] = trajectory_fingerprint
            if fingerprint:
                fingerprints.add(fingerprint)
                accepted[-1].update(scenario_id=result["scenario_id"], scenario_family=result["scenario_family"],
                                    scenario_fingerprint=fingerprint, data_split=data_split)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "train.jsonl"
    media = bundle_dataset_images(samples, output) if bundle_images else None
    training = [row for row in samples if row.get("data_split", "train") == "train"]
    validation = [row for row in samples if row.get("data_split", "train") == "validation"]
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in training))
    if validation:
        (output / "validation.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in validation))
    cases = sorted({row["case_id"] for row in samples})
    manifest = {"stage": "data_generation", "training_started": False, "experiments": experiments,
                "apps": len(cases), "trajectories": len(accepted),
                "samples": len(samples),
                "operation_samples": sum(1 for row in samples if row["phase"] == "operation"),
                "review_samples": sum(1 for row in samples if row["phase"] == "review"),
                "phase_counts": dict(collections.Counter(row["phase"] for row in samples)),
                "cases": cases, "case_source": {case: source_of.get(case, "official") for case in cases},
                "samples_by_app": dict(collections.Counter(row["case_id"] for row in samples)),
                "trajectories_by_app": dict(collections.Counter(run["case_id"] for run in accepted)),
                "eval_holdout": split["eval_holdout"],
                "split_sha256": hashlib.sha256((root / "configs/sft-split.yaml").read_bytes()).hexdigest(),
                "data_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "accepted_runs": accepted, "excluded_runs": excluded}
    if fingerprints:
        manifest.update(unique_scenarios=len(fingerprints), train_samples=len(training), validation_samples=len(validation),
            train_trajectories=sum(run.get("data_split", "train") == "train" for run in accepted),
            validation_trajectories=sum(run.get("data_split", "train") == "validation" for run in accepted),
            scenario_family_counts=dict(collections.Counter(run["scenario_family"] for run in accepted if "scenario_family" in run)),
            split_unit="complete_scenario", review_labels=dict(collections.Counter(
                json.loads(row["messages"][-1]["content"])["verdict"] for row in samples if row["phase"] == "review")))
    manifest["review_profiles"] = dict(collections.Counter(run.get("review_profile", "persistence") for run in accepted))
    if deduplicate_trajectories:
        manifest["trajectory_deduplication"] = {"enabled": True, "unit": "complete_trajectory",
            "key": "case,task,observed_outcome,semantic_action_sequence",
            "removed": sum(run["reason"] == "duplicate_trajectory" for run in excluded)}
    manifest["prompt_sha256"] = {profile: {
        "operation": hashlib.sha256(OPERATION_PROMPTS[profile].encode()).hexdigest(),
        "review": hashlib.sha256(REVIEW_PROMPTS[profile].encode()).hexdigest()}
        for profile in manifest["review_profiles"]}
    if validation:
        manifest["validation_sha256"] = hashlib.sha256((output / "validation.jsonl").read_bytes()).hexdigest()
    if media is not None:
        media_path = output / "media_manifest.json"
        media_path.write_text(json.dumps(media, ensure_ascii=False, indent=2) + "\n")
        manifest.update(images=len(media), image_root=".", image_path_format="relative_to_dataset",
                        media_manifest_sha256=hashlib.sha256(media_path.read_bytes()).hexdigest())
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def bundle_dataset_images(samples, output):
    image_dir = output / "images"
    image_dir.mkdir()
    paths, media = {}, {}
    for sample in samples:
        for message in sample["messages"][:-1]:
            for part in message["content"]:
                if part["type"] != "image":
                    continue
                source = part["image"]
                if source not in paths:
                    data = Path(source).read_bytes()
                    digest = hashlib.sha256(data).hexdigest()
                    relative = f"images/{digest}.png"
                    target = output / relative
                    if not target.exists():
                        shutil.copyfile(source, target)
                    paths[source] = relative
                    media.setdefault(relative, {"sha256": digest, "source_paths": []})["source_paths"].append(source)
                part["image"] = paths[source]
    return media


if __name__ == "__main__":
    main()
