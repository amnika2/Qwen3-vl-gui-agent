import copy
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from guiqa.review_evidence import review_content, review_prompt
from guiqa.sft_data import SftRecorder
from guiqa.sft_review_evidence import build_review_dataset, replay_frames
from guiqa.sft_v2 import checksum, read_rows, trace_history, write_json, write_rows


def source_dataset(root):
    source = root / "datasets/source"
    source.mkdir(parents=True)
    samples = []
    for partition, run_id in (("train", "run-train"), ("validation", "run-validation")):
        directory = root / "runs" / run_id
        (directory / "screenshots").mkdir(parents=True)
        actions = [None, {"action": "type", "text": "New"}, {"action": "click", "coordinate": [500, 600]},
                   {"action": "press", "key": "Control+r"}]
        trace = [{"step": step, "action": action, "execution_error": None} for step, action in enumerate(actions)]
        write_rows(directory / "trace.jsonl", trace)
        write_json(directory / "result.json", {"task": "Set Name to New and verify the saved value."})
        (directory / "private").mkdir()
        write_json(directory / "private/oracle.json", {"expected": "PRIVATE_VALUE_MUST_NOT_ENTER_CONTEXT"})
        for step, color in enumerate(("white", "black", "gray", "white")):
            Image.new("RGB", (128, 90), color).save(directory / f"screenshots/{step:03d}.png")
        sample = {"id": f"{run_id}:terminal_review:3", "case_id": "sweeper-125", "workflow": "target",
                  "phase": "review", "data_split": partition, "scenario_id": run_id,
                  "scenario_fingerprint": run_id, "review_profile": "persistence", "messages": [],
                  "derived_provenance": {"source_run_id": run_id, "history_end_step": 3,
                                          "before_step": 2, "after_step": 3}}
        write_rows(source / f"{partition}.jsonl", [sample])
        samples.append(sample)
    write_json(source / "media_manifest.json", {})
    write_json(source / "manifest.json", {"data_sha256": checksum(source / "train.jsonl"),
                                          "validation_sha256": checksum(source / "validation.jsonl"),
                                          "eval_holdout": ["sweeper-005", "sweeper-007", "sweeper-008", "sweeper-040", "sweeper-041"]})
    return source, samples


def annotated_answer():
    return {"action": "finish", "field_readings": [
                {"field": "Name", "requested_value": "New", "input_value": "New", "input_step": 1,
                 "observed_value": "Old", "observed_step": 3}],
            "submission_step": 2, "requested_value_entered": True, "submission_succeeded": True,
            "final_record_visible": True, "verdict": "fail", "observation": "Name is Old after saving New.",
            "evidence_steps": [1, 2, 3]}


def test_preview_preserves_split_source_bytes_and_uses_shared_inference_content(tmp_path):
    source, samples = source_dataset(tmp_path)
    annotations = tmp_path / "annotations.json"
    write_json(annotations, {"version": "evidence_v1", "annotations": [
        {"sample_id": row["id"], "assistant": annotated_answer(), "note": "Checked the actual screenshots."}
        for row in samples]})
    before = {path.name: path.read_bytes() for path in source.iterdir()}
    output = tmp_path / "datasets/preview"

    manifest, verification = build_review_dataset(tmp_path, source, annotations, output)

    assert verification["passed"] and verification["source_bytes_preserved"]
    assert manifest["train_samples"] == manifest["validation_samples"] == 1
    assert manifest["unannotated_source_reviews"] == 0
    assert {path.name: path.read_bytes() for path in source.iterdir()} == before
    row = read_rows(output / "train.jsonl")[0]
    directory = tmp_path / "runs/run-train"
    history = trace_history(read_rows(directory / "trace.jsonl"))
    frames = replay_frames(directory, history, 2, 3)
    paths = {frame["step"]: str(directory / f"screenshots/{frame['step']:03d}.png") for frame in frames}
    expected_content = review_content("Set Name to New and verify the saved value.", history, frames, paths)
    actual_content = copy.deepcopy(row["messages"][1]["content"])
    for actual, expected in zip(actual_content, expected_content):
        if actual["type"] == "image":
            assert checksum(output / actual["image"]) == checksum(Path(expected["image"]))
            actual["image"] = expected["image"]
    assert actual_content == expected_content
    assert row["messages"][0]["content"][0]["text"] == review_prompt("persistence")
    assert json.loads(row["messages"][-1]["content"]) == annotated_answer()
    assert "PRIVATE_VALUE_MUST_NOT_ENTER_CONTEXT" not in json.dumps(row["messages"])


def test_unannotated_reviews_are_not_filled_from_old_labels(tmp_path):
    source, samples = source_dataset(tmp_path)
    annotations = tmp_path / "annotations.json"
    write_json(annotations, {"version": "evidence_v1", "annotations": [
        {"sample_id": samples[1]["id"], "assistant": annotated_answer()}]})

    manifest, _ = build_review_dataset(tmp_path, source, annotations, tmp_path / "preview")

    assert manifest["samples"] == 1 and manifest["unannotated_source_reviews"] == 1
    assert manifest["train_samples"] == 0 and manifest["validation_samples"] == 1


def test_recorder_new_format_matches_offline_builder(tmp_path):
    source_dataset(tmp_path)
    directory = tmp_path / "runs/run-train"
    history = trace_history(read_rows(directory / "trace.jsonl"))
    result = {"case_id": "sweeper-125", "workflow": "target", "build": "A", "actions": 3}
    context = SimpleNamespace(directory=directory, result=result, history=history, event=lambda _: None)
    recorder = SftRecorder(context, "Set Name to New and verify the saved value.", {"review_format": "evidence_v1"})

    recorder.final_review([1, 2, 3], annotated_answer())

    recorded = read_rows(directory / "sft/samples.jsonl")[0]
    assert recorded["review_format"] == "evidence_v1"
    assert recorded["review_frames"] == replay_frames(directory, history, 2, 3)
    assert recorded["assistant"] == annotated_answer()
    assert recorded["messages"][0]["content"][0]["text"] == review_prompt("persistence")


def test_annotation_cannot_cite_an_unselected_screenshot(tmp_path):
    source, samples = source_dataset(tmp_path)
    answer = annotated_answer()
    answer["field_readings"][0]["input_step"] = 0
    annotations = tmp_path / "annotations.json"
    write_json(annotations, {"version": "evidence_v1", "annotations": [
        {"sample_id": samples[0]["id"], "assistant": answer}]})

    with pytest.raises(ValueError, match="supplied screenshot"):
        build_review_dataset(tmp_path, source, annotations, tmp_path / "preview")
