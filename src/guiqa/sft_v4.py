"""Build atomic, intent-aware reviews from the frozen, visibly annotated v3 data."""

import copy
import json
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path

from .review_context import review_content
from .review_contract import parse_review, review_guard, review_prompt
from .sft_review_evidence import verify_preview
from .sft_v2 import (absolute_images, bundle_images, checksum, read_json, read_rows,
                     trace_history, value_hash, write_json, write_rows)


# These additional readings were inspected on the original screenshots, not inferred from actions.
BASELINE_READINGS = {
    "20261107T160110Z-2ae89c82": ("Subject", "Contact information", None),
    "20261107T160238Z-a5427d2b": ("Subject", "Informations visiteurs", None),
    "20261107T160516Z-25df2862": ("Subject", "Returns policy", None),
    "20261107T160305Z-34ca338f": ("Subject", "Ticket terms", None),
    "20261107T173229Z-b6af7212": ("Title", "About us", 12),
    "20261107T173316Z-c732f64f": ("Title", "About us", 13),
    "20261107T173334Z-318871a5": ("Title", "About us", 12),
    "20261107T173350Z-2483793e": ("Title", "About us", 13),
}

PRESERVED_ZONE_FIELDS = {
    "20261107T173220Z-98690651": {"Code", "Name"},
    "20261107T173306Z-341da193": {"Code", "Description"},
    "20261107T173325Z-f31f3153": {"Name", "Description"},
    "20261107T173342Z-0646b58a": {"Code"},
}


def generate_annotations(root, source, output):
    root, source, output = Path(root).resolve(), Path(source).resolve(), Path(output).resolve()
    parent, hashes, originals = source_rows(source)
    annotations = []
    for sample in originals:
        if sample["phase"] == "review":
            annotations.append(convert_review(root, sample))
    document = {
        "version": "evidence_v2", "source_dataset": str(source.relative_to(root)),
        "source_artifact_sha256": hashes,
        "source_annotation_sha256": parent["annotation_sha256"],
        "additional_reading_basis": "manually inspected original screenshots listed in BASELINE_READINGS",
        "annotations": annotations,
    }
    write_json(output, document)
    return {"reviews": len(annotations), "new_baseline_reviews": len(BASELINE_READINGS),
            "maximum_review_images": max(len(row["frames"]) for row in annotations),
            "labels": dict(Counter(row["assistant"]["verdict"] for row in annotations)),
            "output": str(output), "sha256": checksum(output)}


def build_dataset(root, source, annotations_path, output, curriculum_paths=()):
    root, source = Path(root).resolve(), Path(source).resolve()
    annotations_path, output = Path(annotations_path).resolve(), Path(output).resolve()
    parent, hashes, originals, annotations = load_inputs(source, annotations_path, output)
    samples = []
    for original in originals:
        if original["phase"] == "review":
            sample = annotated_review(root, original, annotations[original["id"]])
        else:
            sample = copy.deepcopy(original)
            absolute_images(sample, source)
        samples.append(sample)
    curriculum = load_curriculum(root, curriculum_paths, parent)
    samples.extend(curriculum)
    media = bundle_images(samples, output)
    for partition in ("train", "validation"):
        write_rows(output / f"{partition}.jsonl", [row for row in samples if row["data_split"] == partition])
        write_rows(output / f"{partition}-reviews.jsonl", [row for row in samples
                                                         if row["data_split"] == partition and row["phase"] == "review"])
    write_json(output / "media_manifest.json", media)
    verification = verify_preview(source, samples, hashes)
    for original, sample in zip(originals, samples):
        if original["data_split"] != sample["data_split"] or original["scenario_fingerprint"] != sample["scenario_fingerprint"]:
            raise ValueError("v4 changed a source scenario partition")
        if original["phase"] == "operation" and original != sample:
            raise ValueError(f"v4 changed an operation sample: {original['id']}")
    reviews = [row for row in samples if row["phase"] == "review"]
    labels = lambda rows: dict(Counter(json.loads(row["messages"][-1]["content"])["verdict"] for row in rows))
    manifest = {
        "version": 4, "stage": "data_generation", "review_format": "evidence_v2",
        "training_started": False, "parent_dataset": str(source.relative_to(root)),
        "parent_artifact_sha256": hashes,
        "annotation_file": str(annotations_path.relative_to(root)), "annotation_sha256": checksum(annotations_path),
        "cases": parent["cases"], "apps": parent["apps"], "eval_holdout": parent["eval_holdout"],
        "trajectory_count": parent["trajectory_count"], "source_trajectories": parent["source_trajectories"],
        "samples": len(samples), "sample_kinds": dict(Counter(row["sample_kind"] for row in samples)),
        "preserved_operation_samples": sum(row["phase"] == "operation" for row in samples),
        "review_samples": len(reviews), "curriculum_reviews": len(curriculum),
        "curriculum_review_runs": len({row["derived_provenance"]["source_run_id"] for row in curriculum}),
        "curriculum_sources": {str(Path(path).resolve().relative_to(root)): checksum(Path(path))
                               for path in curriculum_paths},
        "review_labels": labels(reviews),
        "review_labels_by_split": {partition: labels([row for row in reviews if row["data_split"] == partition])
                                   for partition in ("train", "validation")},
        "train_samples": sum(row["data_split"] == "train" for row in samples),
        "validation_samples": sum(row["data_split"] == "validation" for row in samples),
        "images": len(media), "maximum_review_images": max(len(row["review_frames"]) for row in reviews),
        "split_unit": "complete_source_scenario", "data_sha256": checksum(output / "train.jsonl"),
        "validation_sha256": checksum(output / "validation.jsonl"),
        "media_manifest_sha256": checksum(output / "media_manifest.json"),
    }
    if len({row["id"] for row in samples}) != len(samples):
        raise ValueError("duplicate v4 sample id")
    verification.update(preserved_operation_samples=sum(row["phase"] == "operation" for row in samples),
                        operation_samples_unchanged=True, all_source_reviews_replaced=True,
                        frozen_holdout_excluded=True, additional_baseline_reviews=len(BASELINE_READINGS))
    write_json(output / "manifest.json", manifest)
    write_json(output / "verification.json", verification)
    return manifest, verification


def load_curriculum(root, paths, parent):
    samples = []
    for path in paths:
        for raw in read_rows(Path(path)):
            sample = copy.deepcopy(raw)
            sample["id"] = f"{sample['source_run_id']}:review_curriculum:{sample['step']}"
            if sample["case_id"] not in parent["cases"] or sample["case_id"] in parent["eval_holdout"]:
                raise ValueError("curriculum must use the ten training applications")
            if sample["data_split"] not in ("train", "validation"):
                raise ValueError("curriculum requires its complete scenario partition")
            sample["messages"][-1] = {"role": "assistant", "content": json.dumps(sample.pop("assistant"), ensure_ascii=False)}
            sample.update(phase="review", sample_kind="review_curriculum", review_format="evidence_v2")
            sample["derived_provenance"]["curriculum"] = True
            sample["derived_provenance"].update(source_review_file=str(Path(path).resolve().relative_to(root)),
                                                source_review_sha256=checksum(Path(path)))
            frames = sample["review_frames"]
            steps = {frame["step"] for frame in frames}
            answer = parse_review(sample["messages"][-1]["content"], steps, sample["review_profile"])
            reason = review_guard(answer, sample["review_profile"])
            if reason or len(frames) > 5:
                raise ValueError(f"invalid curriculum review {sample['id']}: {reason}")
            directory = Path(sample["run_directory"])
            sample["derived_provenance"]["source_run_path"] = str(directory.relative_to(root))
            if checksum(directory / "trace.jsonl") != sample["derived_provenance"]["source_trace_sha256"]:
                raise ValueError(f"curriculum source trace changed: {directory.name}")
            images = [item for message in sample["messages"][:-1] for item in message["content"] if item["type"] == "image"]
            if len(images) != len(frames):
                raise ValueError("curriculum image count differs from supplied frames")
            for item, frame in zip(images, frames):
                expected = directory / f"screenshots/{frame['step']:03d}.png"
                if checksum(Path(item["image"])) != checksum(expected):
                    raise ValueError("curriculum screenshot does not match its original step")
            samples.append(sample)
    return samples


def source_rows(source):
    parent = read_json(source / "manifest.json")
    hashes = {name: checksum(source / name) for name in
              ("manifest.json", "train.jsonl", "validation.jsonl", "media_manifest.json")}
    if hashes["train.jsonl"] != parent["data_sha256"] or hashes["validation.jsonl"] != parent["validation_sha256"]:
        raise ValueError("source differs from its manifest")
    originals = [row for partition in ("train", "validation") for row in read_rows(source / f"{partition}.jsonl")]
    if any(row["case_id"] in parent["eval_holdout"] for row in originals):
        raise ValueError("frozen evaluation applications cannot enter v4")
    return parent, hashes, originals


def load_inputs(source, annotations_path, output):
    parent, hashes, originals = source_rows(source)
    document = read_json(annotations_path)
    reviews = {row["id"] for row in originals if row["phase"] == "review"}
    annotations = {row["sample_id"]: row for row in document["annotations"]}
    if (document["version"] != "evidence_v2" or document["source_artifact_sha256"] != hashes
            or set(annotations) != reviews or len(annotations) != len(document["annotations"])):
        raise ValueError("v4 annotations must cover every frozen source review exactly once")
    if output == source or source in output.parents:
        raise ValueError("v4 output must be outside preserved v3")
    output.mkdir(parents=True, exist_ok=False)
    return parent, hashes, originals, annotations


def convert_review(root, sample):
    old = json.loads(sample["messages"][-1]["content"])
    run_id = sample["derived_provenance"]["source_run_id"]
    task = read_json(root / "runs" / run_id / "result.json")["task"]
    frames = copy.deepcopy(sample["review_frames"])
    task_kind = "create" if ("Create a new Geo Zone" in task or "Add a new child page" in task
                             or sample["case_id"] == "sweeper-107") else "edit"
    readings = []
    note = "Atomic conversion of the frozen v3 visible readings; public task determines field intent."
    baseline = BASELINE_READINGS.get(run_id)
    for reading in old["field_readings"]:
        intent = field_intent(sample["case_id"], run_id, task, task_kind, reading)
        before = None if reading["input_value"] is None else {
            "value": reading["input_value"], "step": reading["input_step"]}
        after = None if reading["observed_value"] is None else {
            "value": reading["observed_value"], "step": reading["observed_step"]}
        if baseline and reading["field"] == baseline[0]:
            before = {"value": baseline[1], "step": 0}
            add_frame(frames, 0, "preservation_baseline")
            if baseline[2] is not None:
                after = {"value": baseline[1], "step": baseline[2]}
                add_frame(frames, baseline[2], "reopened_preserved_field")
            note += f" Manually read {baseline[0]}={baseline[1]!r} at step 0."
            if baseline[2] is not None:
                note += f" Its actual reopened reading is step {baseline[2]}, correcting the old Metadata-tab citation."
        requested = reading["requested_value"]
        compared = before["value"] if intent == "preserve" and requested == "unchanged" and before else requested
        readings.append({"field": reading["field"], "intent": intent, "requested_value": requested,
                         "before": before, "after": after,
                         "input_matches_request": compare_reading(sample["case_id"], reading["field"], requested, before)
                         if intent == "edit" else None,
                         "result_matches_request": compare_reading(sample["case_id"], reading["field"], compared, after)
                         if requested != "unchanged" or before else None})
    if old["submission_succeeded"]:
        status = "accepted"
    elif old.get("submission_observed") and old.get("business_rejection_visible"):
        status = "rejected"
    else:
        status = "unknown"
    submission = old["submission_step"]
    answer = {"action": "finish", "task_kind": task_kind, "field_readings": readings,
              "submission": {"status": status, "evidence": None if submission is None else {
                  "step": submission, "observation": old["observation"]}},
              "result_observed": old["final_record_visible"],
              "result_steps": sorted({row["after"]["step"] for row in readings if row["after"]})
              if old["final_record_visible"] else [],
              "expected_rejection": old.get("expected_rejection", False), "verdict": old["verdict"],
              "observation": old["observation"],
              "evidence_steps": sorted(set(old["evidence_steps"]) | {row["before"]["step"] for row in readings
                                                                     if row["before"]}
                                       | {row["after"]["step"] for row in readings if row["after"]})}
    if len(frames) > 5:
        raise ValueError(f"{sample['id']}: {len(frames)} annotated screenshots exceed the five-frame budget")
    frames.sort(key=lambda row: row["step"])
    selected = {row["step"] for row in frames}
    parse_review(json.dumps(answer), selected, sample["review_profile"])
    reason = review_guard(answer, sample["review_profile"])
    if reason:
        raise ValueError(f"{sample['id']}: {reason}")
    return {"sample_id": sample["id"], "source_sample_sha256": value_hash(sample),
            "frames": frames, "assistant": answer, "note": note}


def field_intent(case, run_id, task, task_kind, reading):
    field = reading["field"]
    if field in ("Login result", "Original-password login"):
        return "generated"
    if (reading["requested_value"] == "unchanged" or "(preserved)" in field
            or case == "sweeper-125" and field == "Option Value"
            or case == "sweeper-142" and field == "IP address"
            or case == "sweeper-195" and field == "Slug" and task_kind == "edit"
            or case == "sweeper-053" and field == "Subject" and "Set Subject" not in task
            or case == "sweeper-053" and field == "Content" and "Keep Content unchanged" in task
            or case == "sweeper-105" and field in PRESERVED_ZONE_FIELDS.get(run_id, set())
            or case == "sweeper-105" and field == "Saved country rows" and reading["requested_value"] == ""
            or case == "sweeper-107" and field == "Product Version" and reading["requested_value"] == ""):
        return "preserve"
    return "edit"


def compare_reading(case, field, requested, reading):
    if reading is None:
        return None
    value = reading["value"]
    if case == "sweeper-125" and field == "Weight":
        return Decimal(value.lstrip("+")) == Decimal(requested)
    if case == "sweeper-107" and field == "Category":
        value = value.removeprefix("[All Projects] ")
    if case == "sweeper-107" and field == "Summary":
        value = re.sub(r"^\d{7}: ", "", value)
    if case == "sweeper-105" and field == "Saved country rows" and requested == "" and value == "-- Select --":
        return True
    return value == requested


def add_frame(frames, step, role):
    frame = next((row for row in frames if row["step"] == step), None)
    if frame:
        frame["roles"].append(role)
    else:
        frames.append({"step": step, "roles": [role]})


def annotated_review(root, original, annotation):
    if annotation["source_sample_sha256"] != value_hash(original):
        raise ValueError(f"source sample changed: {original['id']}")
    provenance = original["derived_provenance"]
    directory = root / "runs" / provenance["source_run_id"]
    if checksum(directory / "trace.jsonl") != provenance["source_trace_sha256"]:
        raise ValueError(f"source trace changed: {directory.name}")
    after = provenance["history_end_step"]
    history = trace_history([row for row in read_rows(directory / "trace.jsonl") if row["step"] <= after])
    frames, answer = annotation["frames"], annotation["assistant"]
    steps = [frame["step"] for frame in frames]
    parse_review(json.dumps(answer), set(steps), original["review_profile"])
    reason = review_guard(answer, original["review_profile"])
    if reason or len(steps) > 5 or len(set(steps)) != len(steps) or any(step > after for step in steps):
        raise ValueError(f"invalid annotated v4 review: {original['id']}: {reason}")
    task = read_json(directory / "result.json")["task"]
    paths = {step: str(directory / f"screenshots/{step:03d}.png") for step in steps}
    sample = {key: copy.deepcopy(original[key]) for key in
              ("case_id", "workflow", "data_split", "scenario_id", "scenario_fingerprint", "review_profile", "sample_kind")}
    sample.update(id=original["id"] + ":evidence_v2", phase="review", review_format="evidence_v2",
                  review_frames=copy.deepcopy(frames),
                  messages=[{"role": "system", "content": [{"type": "text", "text": review_prompt(original["review_profile"])}]},
                            {"role": "user", "content": review_content(task, history, frames, paths)},
                            {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)}],
                  derived_provenance={**copy.deepcopy(provenance), "source_sample_id": original["id"],
                                      "source_sample_sha256": value_hash(original), "screenshot_steps": steps,
                                      "label_basis": "explicit_visible_field_reading_annotation",
                                      "annotation": annotation})
    return sample
