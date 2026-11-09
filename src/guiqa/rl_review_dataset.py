"""Bundle frozen SFT reviews and verified real-run curriculum for review RL."""

import argparse
import copy
import json
from collections import Counter
from pathlib import Path

import yaml

from .rl_review_reward import validate_gold
from .sft_v2 import absolute_images, bundle_images, checksum, read_json, read_rows, write_json, write_rows


ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("datasets/sft-main-10apps-v4"))
    parser.add_argument("--curriculum", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, default=Path("datasets/rl-review-pilot-v1"))
    parser.add_argument("--new-only", action="store_true")
    args = parser.parse_args()
    manifest, verification = build_dataset(ROOT, args.source, args.curriculum, args.output, args.new_only)
    print(json.dumps({"manifest": manifest, "verification": verification}, ensure_ascii=False, indent=2))


def build_dataset(root, source, curriculum_paths, output, new_only=False):
    root = Path(root).resolve()
    source, output = (root / source).resolve(), (root / output).resolve()
    paths = [(root / path).resolve() for path in curriculum_paths]
    if output == source or source in output.parents:
        raise ValueError("RL output must be outside the preserved source dataset")
    split_path = root / "configs/sft-split.yaml"
    split = yaml.safe_load(split_path.read_text())
    original, source_hashes = _source_reviews(source)
    curriculum, batches, run_sources = _curriculum_reviews(root, paths)
    gold = _validate_reviews(original + curriculum, split)
    samples = curriculum if new_only else original + curriculum
    if not samples:
        raise ValueError("RL review dataset must contain at least one sample")

    output.mkdir(parents=True, exist_ok=False)
    media = bundle_images(samples, output)
    for partition in ("train", "validation"):
        write_rows(output / f"{partition}.jsonl", [row for row in samples if row["data_split"] == partition])
    write_json(output / "media_manifest.json", media)
    labels = lambda rows: dict(Counter(gold[row["id"]]["verdict"] for row in rows))
    manifest = {
        "version": 1, "stage": "review_rl_data", "review_format": "evidence_v2", "training_started": False,
        "source_dataset": str(source), "source_artifact_sha256": source_hashes,
        "curriculum_sources": batches, "curriculum_runs": run_sources, "new_only": new_only,
        "samples": len(samples), "source_review_samples": 0 if new_only else len(original),
        "curriculum_review_samples": len(curriculum), "review_samples": len(samples), "operation_samples": 0,
        "train_samples": sum(row["data_split"] == "train" for row in samples),
        "validation_samples": sum(row["data_split"] == "validation" for row in samples),
        "review_labels": labels(samples),
        "review_labels_by_split": {partition: labels([row for row in samples if row["data_split"] == partition])
                                   for partition in ("train", "validation")},
        "cases": sorted({row["case_id"] for row in samples}), "eval_holdout": split["eval_holdout"],
        "split_unit": "complete_source_scenario", "split_config_sha256": checksum(split_path),
        "split_boundaries": {partition: {
            "scenario_ids": sorted({row["scenario_id"] for row in samples if row["data_split"] == partition}),
            "scenario_fingerprints": sorted({row["scenario_fingerprint"] for row in samples if row["data_split"] == partition}),
            "source_run_ids": sorted({row["derived_provenance"]["source_run_id"] for row in samples
                                      if row["data_split"] == partition}),
        } for partition in ("train", "validation")},
        "pixel_review": {
            "new_sample_status_counts": dict(Counter(row["derived_provenance"].get("human_pixel_review", "unspecified")
                                                     for row in curriculum)),
            "coverage": "Representative screenshot inspection only; not a full human review of every new sample.",
            "source_annotation_status": "Frozen v4 source annotations retained without relabelling.",
        },
        "images": len(media), "maximum_review_images": max(len(row["review_frames"]) for row in samples),
        "data_sha256": checksum(output / "train.jsonl"),
        "validation_sha256": checksum(output / "validation.jsonl"),
        "media_manifest_sha256": checksum(output / "media_manifest.json"),
    }
    for name, expected in source_hashes.items():
        if checksum(source / name) != expected:
            raise ValueError("preserved source changed during build: " + name)
    for relative, record in media.items():
        if checksum(output / relative) != record["sha256"]:
            raise ValueError("bundled screenshot differs from its original: " + relative)
    verification = {
        "passed": True, "samples": len(samples), "review_only": True,
        "source_bytes_preserved": True, "source_partitions_and_gold_preserved": True,
        "frozen_holdout_excluded": True, "scenario_id_overlap": 0,
        "scenario_fingerprint_overlap": 0, "source_run_overlap": 0,
        "curriculum_batches_complete": True, "curriculum_source_artifacts_verified": True,
        "curriculum_deployment_commits_verified": True, "images_byte_identical": True,
        "full_new_sample_human_pixel_review": False,
    }
    write_json(output / "manifest.json", manifest)
    write_json(output / "verification.json", verification)
    return manifest, verification


def _source_reviews(source):
    manifest = read_json(source / "manifest.json")
    hashes = {name: checksum(source / name) for name in
              ("manifest.json", "train.jsonl", "validation.jsonl", "media_manifest.json")}
    for name, key in (("train.jsonl", "data_sha256"), ("validation.jsonl", "validation_sha256"),
                      ("media_manifest.json", "media_manifest_sha256")):
        if hashes[name] != manifest[key]:
            raise ValueError("source differs from its manifest: " + name)
    media = read_json(source / "media_manifest.json")
    reviews, verified_images = [], set()
    for partition in ("train", "validation"):
        for original in read_rows(source / f"{partition}.jsonl"):
            if original["phase"] != "review":
                continue
            if original["data_split"] != partition:
                raise ValueError("source review differs from its frozen partition")
            row = copy.deepcopy(original)
            _normalize_answer(row)
            for item in _images(row):
                relative = item["image"]
                if relative not in verified_images:
                    if checksum(source / relative) != media[relative]["sha256"]:
                        raise ValueError("source screenshot differs from its media manifest: " + relative)
                    verified_images.add(relative)
            absolute_images(row, source)
            reviews.append(row)
    if len(reviews) != manifest["review_samples"]:
        raise ValueError("source review count differs from its manifest")
    return reviews, hashes


def _curriculum_reviews(root, paths):
    samples, batches, run_sources = [], [], []
    for path in paths:
        batch = read_json(path.parent / "manifest.json")
        if not batch.get("complete") or batch["calibration"]:
            raise ValueError("curriculum batch must be complete and non-calibration: " + str(path.parent))
        for name, expected in batch["dataset_sha256"].items():
            if checksum(path.parent / name) != expected:
                raise ValueError("curriculum batch dataset hash differs: " + name)
        rows = read_rows(path)
        completed = {record["run_id"]: record for record in batch["completed"]}
        if (len(rows) != batch["samples_planned"] or len(completed) != len(rows)
                or len(completed) != len(batch["completed"])
                or {row["derived_provenance"]["source_run_id"] for row in rows} != set(completed)):
            raise ValueError("curriculum batch does not contain every planned completed run")
        for raw in rows:
            row = copy.deepcopy(raw)
            _normalize_answer(row)
            record = completed[row["derived_provenance"]["source_run_id"]]
            run_sources.append(_verify_curriculum_run(root, row, record, path.parent))
            absolute_images(row, path.parent)
            samples.append(row)
        batches.append({"path": str(path), "sha256": checksum(path),
                        "manifest_sha256": checksum(path.parent / "manifest.json"), "samples": len(rows)})
    return samples, batches, run_sources


def _verify_curriculum_run(root, row, record, batch):
    directory = Path(row["run_directory"])
    provenance = row["derived_provenance"]
    result = read_json(directory / "result.json")
    if result != record or not result["complete"] or row["calibration"]:
        raise ValueError("curriculum result differs from its completed collector record")
    for key in ("case_id", "workflow", "build", "scenario_id", "data_split"):
        if row[key] != result[key]:
            raise ValueError("curriculum metadata differs from its result: " + key)
    hashes = result["source_hashes"]
    for name, expected in hashes.items():
        if checksum(directory / name) != expected:
            raise ValueError("curriculum source artifact changed: " + str(directory / name))
    if provenance["source_trace_sha256"] != hashes["trace.jsonl"]:
        raise ValueError("curriculum trace provenance differs from its result")
    deployment = directory / "private/instance/instance.json"
    if (Path(provenance["deployment_manifest"]).resolve() != deployment.resolve()
            or provenance["deployment_manifest_sha256"] != hashes["private/instance/instance.json"]):
        raise ValueError("curriculum deployment provenance differs from its result")
    instance = read_json(deployment)
    case = read_json(root / "cases" / row["case_id"] / "private.json")
    commit = case["base_commit" if row["build"] == "A" else "reference_commit"]
    if instance["case_id"] != row["case_id"] or instance["build"] != row["build"] or instance["commit"] != commit:
        raise ValueError("curriculum deployment commit differs from the pinned case build")
    steps = [frame["step"] for frame in row["review_frames"]]
    if steps != result["screenshot_steps"] or steps != provenance["screenshot_steps"] or steps != row["screenshot_steps"]:
        raise ValueError("curriculum selected steps differ from its collector result")
    trace = {entry["step"]: entry for entry in read_rows(directory / "trace.jsonl")}
    if max(trace) != result["actions"]:
        raise ValueError("curriculum trace length differs from its collector result")
    images = _images(row)
    if len(images) != len(steps):
        raise ValueError("curriculum image count differs from selected steps")
    for step, item in zip(steps, images):
        relative = f"screenshots/{step:03d}.png"
        if (trace[step]["screenshot"] != relative or trace[step]["run_id"] != result["run_id"]
                or checksum(batch / item["image"]) != hashes[relative]):
            raise ValueError("curriculum screenshot differs from its actual trace step")
    answer = json.loads(row["messages"][-1]["content"])
    if answer["verdict"] != result["verdict"] or answer != read_json(directory / "label.json"):
        raise ValueError("curriculum gold differs from its collector label and result")
    return {"id": row["id"], "source_run_id": provenance["source_run_id"], "directory": str(directory),
            "result_sha256": checksum(directory / "result.json"), "instance_commit": instance["commit"],
            "pinned_case_sha256": checksum(root / "cases" / row["case_id"] / "private.json"),
            "source_hashes": hashes}


def _validate_reviews(rows, split):
    allowed = set(split["training_pilot"] + split["training_supplementary"])
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("RL review IDs must be unique")
    gold = {}
    for row in rows:
        if (row["phase"] != "review" or row["review_format"] != "evidence_v2"
                or row["case_id"] not in allowed or row["case_id"] in split["eval_holdout"]
                or row["data_split"] not in ("train", "validation")):
            raise ValueError("RL reviews require evidence_v2, a training application, and a frozen partition")
        if any(message["role"] == "assistant" for message in row["messages"][:-1]):
            raise ValueError("review prompt contains an assistant answer")
        steps = {frame["step"] for frame in row["review_frames"]}
        if not 1 <= len(steps) <= 5 or len(_images(row)) != len(steps):
            raise ValueError("review must contain one to five uniquely identified screenshots")
        gold[row["id"]] = validate_gold(json.loads(row["messages"][-1]["content"]), steps, row["review_profile"])
    for key in ("scenario_id", "scenario_fingerprint", "source_run_id"):
        values = {partition: {row["derived_provenance"][key] if key == "source_run_id" else row[key]
                              for row in rows if row["data_split"] == partition}
                  for partition in ("train", "validation")}
        if values["train"] & values["validation"]:
            raise ValueError("training and validation overlap on " + key)
    return gold


def _normalize_answer(row):
    message = row["messages"][-1]
    if message["role"] != "assistant":
        raise ValueError("review sample must end with its assistant gold answer")
    if isinstance(message["content"], list):
        if len(message["content"]) != 1 or message["content"][0]["type"] != "text":
            raise ValueError("assistant gold must contain exactly one text answer")
        message["content"] = message["content"][0]["text"]


def _images(row):
    return [item for message in row["messages"][:-1] for item in message["content"] if item["type"] == "image"]
