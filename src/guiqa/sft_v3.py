"""Replace v2 reviews with explicitly transcribed evidence_v1 annotations."""

import copy
import json
from collections import Counter
from pathlib import Path

from .sft_review_evidence import annotated_review, load_inputs, verify_preview
from .sft_v2 import (absolute_images, bundle_images, checksum, read_rows, value_hash,
                     write_json, write_rows)


def build_dataset(root, source, annotations_path, output):
    root, source = Path(root).resolve(), Path(source).resolve()
    annotations_path, output = Path(annotations_path).resolve(), Path(output).resolve()
    parent, source_hashes, originals, annotations = load_v3_inputs(source, annotations_path, output)
    samples = []
    for original in originals:
        if original["phase"] == "review":
            sample = annotated_review(root, original, annotations[original["id"]])
            sample["sample_kind"] = original["sample_kind"]
            sample["derived_provenance"].update(source_sample_sha256=value_hash(original),
                                                source_sample_kind=original["sample_kind"])
        else:
            sample = copy.deepcopy(original)
            absolute_images(sample, source)
        samples.append(sample)
    media = bundle_images(samples, output)
    for partition in ("train", "validation"):
        write_rows(output / f"{partition}.jsonl", [row for row in samples if row["data_split"] == partition])
    write_json(output / "media_manifest.json", media)
    verification = verify_v3(source, originals, samples, source_hashes)
    reviews = [row for row in samples if row["phase"] == "review"]
    manifest = {
        "version": 3, "stage": "data_generation", "review_format": "evidence_v1",
        "training_started": False, "parent_dataset": str(source.relative_to(root)),
        "parent_artifact_sha256": source_hashes,
        "annotation_file": str(annotations_path.relative_to(root)),
        "annotation_sha256": checksum(annotations_path),
        "cases": parent["cases"], "apps": parent["apps"], "eval_holdout": parent["eval_holdout"],
        "trajectory_count": parent["trajectory_count"], "source_trajectories": parent["source_trajectories"],
        "samples": len(samples), "sample_kinds": dict(Counter(row["sample_kind"] for row in samples)),
        "preserved_operation_samples": len(samples) - len(reviews), "review_samples": len(reviews),
        "review_labels": dict(Counter(json.loads(row["messages"][-1]["content"])["verdict"] for row in reviews)),
        "review_labels_by_split": {partition: dict(Counter(json.loads(row["messages"][-1]["content"])["verdict"]
                                                         for row in reviews if row["data_split"] == partition))
                                   for partition in ("train", "validation")},
        "train_samples": sum(row["data_split"] == "train" for row in samples),
        "validation_samples": sum(row["data_split"] == "validation" for row in samples),
        "images": len(media), "maximum_review_images": 4, "split_unit": "complete_source_scenario",
        "data_sha256": checksum(output / "train.jsonl"),
        "validation_sha256": checksum(output / "validation.jsonl"),
        "media_manifest_sha256": checksum(output / "media_manifest.json"),
    }
    write_json(output / "manifest.json", manifest)
    write_json(output / "verification.json", verification)
    return manifest, verification


def load_v3_inputs(source, annotations_path, output):
    parent, hashes, reviews, annotations = load_inputs(source, annotations_path, output)
    by_id = {annotation["sample_id"]: annotation for annotation in annotations}
    missing = reviews.keys() - by_id.keys()
    if missing:
        raise ValueError(f"v3 requires every source review to be annotated; missing {len(missing)}")
    originals = [row for partition in ("train", "validation") for row in read_rows(source / f"{partition}.jsonl")]
    if any(row["case_id"] in parent["eval_holdout"] for row in originals):
        raise ValueError("frozen evaluation applications cannot enter v3 training data")
    return parent, hashes, originals, by_id


def verify_v3(source, originals, samples, source_hashes):
    verification = verify_preview(source, samples, source_hashes)
    if len({sample["id"] for sample in samples}) != len(originals):
        raise ValueError("v3 must replace source samples one for one")
    for original, sample in zip(originals, samples):
        if original["data_split"] != sample["data_split"] or original["scenario_fingerprint"] != sample["scenario_fingerprint"]:
            raise ValueError("v3 changed a source scenario partition")
        if original["phase"] == "operation" and original != sample:
            raise ValueError(f"v3 changed an operation sample: {original['id']}")
    verification.update(preserved_operation_samples=sum(row["phase"] == "operation" for row in samples),
                        all_source_reviews_replaced=True, operation_samples_unchanged=True)
    return verification
