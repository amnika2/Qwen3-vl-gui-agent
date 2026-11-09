"""Build explicitly annotated review examples with the inference evidence format."""

import copy
import json
from collections import Counter
from pathlib import Path

from .control import LoopControl, review_guard
from .review_evidence import parse_review, review_content, review_prompt, select_evidence
from .sft_v2 import bundle_images, checksum, read_json, read_rows, trace_history, write_json, write_rows


def build_review_dataset(root, source, annotations_path, output):
    root, source = Path(root).resolve(), Path(source).resolve()
    annotations_path, output = Path(annotations_path).resolve(), Path(output).resolve()
    parent, source_hashes, reviews, annotations = load_inputs(source, annotations_path, output)
    samples = []
    for annotation in annotations:
        sample = reviews[annotation["sample_id"]]
        samples.append(annotated_review(root, sample, annotation))
    media = bundle_images(samples, output)
    for partition in ("train", "validation"):
        write_rows(output / f"{partition}.jsonl", [row for row in samples if row["data_split"] == partition])
    write_json(output / "media_manifest.json", media)
    verification = verify_preview(source, samples, source_hashes)
    manifest = {"version": "evidence_v1", "stage": "annotated_review_preview", "training_started": False,
                "source_dataset": str(source), "source_artifact_sha256": source_hashes,
                "annotation_file": str(annotations_path), "annotation_sha256": checksum(annotations_path),
                "cases": sorted({row["case_id"] for row in samples}), "eval_holdout": parent["eval_holdout"],
                "samples": len(samples), "review_labels": dict(Counter(
                    json.loads(row["messages"][-1]["content"])["verdict"] for row in samples)),
                "train_samples": sum(row["data_split"] == "train" for row in samples),
                "validation_samples": sum(row["data_split"] == "validation" for row in samples),
                "source_reviews": len(reviews), "unannotated_source_reviews": len(reviews) - len(samples),
                "maximum_review_images": 4, "split_unit": "complete_source_scenario", "images": len(media),
                "data_sha256": checksum(output / "train.jsonl"),
                "validation_sha256": checksum(output / "validation.jsonl"),
                "media_manifest_sha256": checksum(output / "media_manifest.json")}
    write_json(output / "manifest.json", manifest)
    write_json(output / "verification.json", verification)
    return manifest, verification


def verify_preview(source, samples, source_hashes):
    partitions = {partition: {row["scenario_fingerprint"] for row in samples if row["data_split"] == partition}
                  for partition in ("train", "validation")}
    if partitions["train"] & partitions["validation"]:
        raise ValueError("source scenarios overlap across train and validation")
    for name, expected in source_hashes.items():
        if checksum(source / name) != expected:
            raise ValueError(f"preserved source changed: {name}")
    return {"passed": True, "samples": len(samples), "source_bytes_preserved": True,
            "whole_scenario_partition_overlap": 0, "explicit_reading_annotations_only": True,
            "selected_screenshot_references_only": True, "shared_inference_review_format": True}


def load_inputs(source, annotations_path, output):
    if output == source or source in output.parents:
        raise ValueError("review output must be outside the preserved source directory")
    parent = read_json(source / "manifest.json")
    source_hashes = {name: checksum(source / name) for name in
                     ("manifest.json", "train.jsonl", "validation.jsonl", "media_manifest.json")}
    if source_hashes["train.jsonl"] != parent["data_sha256"] or source_hashes["validation.jsonl"] != parent["validation_sha256"]:
        raise ValueError("source content differs from its manifest")
    reviews = {}
    for partition in ("train", "validation"):
        for sample in read_rows(source / f"{partition}.jsonl"):
            if sample["phase"] == "review":
                reviews[sample["id"]] = sample
    document = read_json(annotations_path)
    if document["version"] != "evidence_v1" or not document["annotations"]:
        raise ValueError("annotations require evidence_v1 and at least one explicitly annotated review")
    seen = set()
    for annotation in document["annotations"]:
        identifier = annotation["sample_id"]
        if identifier in seen or identifier not in reviews:
            raise ValueError(f"duplicate or unknown source review: {identifier}")
        seen.add(identifier)
        if reviews[identifier]["case_id"] in parent["eval_holdout"]:
            raise ValueError("frozen evaluation applications cannot enter review training data")
    output.mkdir(parents=True, exist_ok=False)
    return parent, source_hashes, reviews, document["annotations"]


def annotated_review(root, source_sample, annotation):
    provenance = source_sample["derived_provenance"]
    directory = root / "runs" / provenance["source_run_id"]
    trace_hash = checksum(directory / "trace.jsonl")
    if "source_trace_sha256" in provenance and trace_hash != provenance["source_trace_sha256"]:
        raise ValueError(f"{source_sample['id']}: source execution trace differs from the frozen dataset")
    trace = read_rows(directory / "trace.jsonl")
    result = read_json(directory / "result.json")
    after = provenance["history_end_step"]
    history = trace_history([row for row in trace if row["step"] <= after])
    before = provenance.get("before_step", after - 1)
    if source_sample["review_profile"] == "business_outcome_v1":
        before = after
    frames = replay_frames(directory, history, before, after)
    selected_steps = {frame["step"] for frame in frames}
    profile = source_sample["review_profile"]
    answer = parse_review(json.dumps(annotation["assistant"]), selected_steps, profile)
    reason = review_guard(answer, profile)
    if reason:
        raise ValueError(f"{source_sample['id']}: annotated verdict lacks required evidence: {reason}")
    paths = {frame["step"]: str(directory / f"screenshots/{frame['step']:03d}.png") for frame in frames}
    for path in paths.values():
        if not Path(path).is_file():
            raise FileNotFoundError(path)
    sample = {key: copy.deepcopy(source_sample[key]) for key in
              ("case_id", "workflow", "data_split", "scenario_id", "scenario_fingerprint", "review_profile")}
    sample.update(id=source_sample["id"] + ":evidence_v1", phase="review", sample_kind="annotated_review",
                  review_format="evidence_v1", review_frames=frames,
                  messages=[{"role": "system", "content": [{"type": "text", "text": review_prompt(profile)}]},
                            {"role": "user", "content": review_content(result["task"], history, frames, paths)},
                            {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)}],
                  derived_provenance={"source_sample_id": source_sample["id"], "source_run_id": directory.name,
                                      "source_trace_sha256": trace_hash,
                                      "history_end_step": after, "before_step": before, "after_step": after,
                                      "screenshot_steps": [frame["step"] for frame in frames],
                                      "label_basis": "explicit_visible_field_reading_annotation",
                                      "annotation": annotation})
    return sample


def replay_frames(directory, history, before, after):
    control = LoopControl(directory / "screenshots/000.png")
    differences = {}
    for row in history:
        if 0 < row["step"] <= before:
            control.record(row["action"], row["step"], directory / f"screenshots/{row['step']:03d}.png")
            differences[row["step"]] = control.difference
    return select_evidence(history, differences, before, after)
