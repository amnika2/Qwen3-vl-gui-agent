import json

import pytest
from PIL import Image

from guiqa.sft_v2 import checksum, read_rows, write_json, write_rows
from guiqa.sft_v3 import build_dataset


def source_with_annotations(root):
    source = root / "datasets/source"
    source.mkdir(parents=True)
    annotations = []
    for partition in ("train", "validation"):
        directory = root / "runs" / partition
        (directory / "screenshots").mkdir(parents=True)
        trace = [{"step": step, "action": action, "execution_error": None}
                 for step, action in enumerate([None, {"action": "type", "text": "New"},
                                                {"action": "click", "coordinate": [500, 500]},
                                                {"action": "press", "key": "Control+r"}])]
        for step, color in enumerate(("white", "black", "gray", "white")):
            Image.new("RGB", (128, 90), color).save(directory / f"screenshots/{step:03d}.png")
        write_rows(directory / "trace.jsonl", trace)
        write_json(directory / "result.json", {"task": "Set Name to New, save and reopen."})
        metadata = {"case_id": "sweeper-185", "workflow": "target", "data_split": partition,
                    "scenario_id": partition, "scenario_fingerprint": partition, "review_profile": "persistence"}
        sha = checksum(directory / "screenshots/000.png")
        (source / "images").mkdir(exist_ok=True)
        (source / "images" / f"{sha}.png").write_bytes((directory / "screenshots/000.png").read_bytes())
        operation = {"id": f"{partition}:0", **metadata, "phase": "operation", "sample_kind": "operation_action",
                     "messages": [{"role": "user", "content": [{"type": "image", "image": f"images/{sha}.png"}]},
                                  {"role": "assistant", "content": '{"action":"type","text":"New"}'}]}
        review = {"id": f"{partition}:review", **metadata, "phase": "review", "sample_kind": "terminal_review",
                  "derived_provenance": {"source_run_id": partition, "history_end_step": 3,
                                          "before_step": 2, "after_step": 3}}
        write_rows(source / f"{partition}.jsonl", [operation, review])
        annotations.append({"sample_id": review["id"], "assistant": {
            "action": "finish", "field_readings": [{"field": "Name", "requested_value": "New",
                "input_value": "New", "input_step": 1, "observed_value": "Old", "observed_step": 3}],
            "submission_step": 2, "requested_value_entered": True, "submission_succeeded": True,
            "final_record_visible": True, "verdict": "fail", "observation": "Old remains after saving New.",
            "evidence_steps": [1, 2, 3]}})
    write_json(source / "media_manifest.json", {})
    write_json(source / "manifest.json", {"cases": ["sweeper-185"], "apps": 1, "trajectory_count": 2,
        "source_trajectories": [], "eval_holdout": ["sweeper-005"],
        "data_sha256": checksum(source / "train.jsonl"),
        "validation_sha256": checksum(source / "validation.jsonl")})
    path = root / "annotations.json"
    write_json(path, {"version": "evidence_v1", "annotations": annotations})
    return source, path


def test_v3_preserves_operations_and_replaces_every_review(tmp_path):
    source, annotations = source_with_annotations(tmp_path)
    original = {partition: read_rows(source / f"{partition}.jsonl") for partition in ("train", "validation")}
    source_bytes = {path.name: path.read_bytes() for path in source.glob("*.json*")}
    output = tmp_path / "datasets/v3"

    manifest, verification = build_dataset(tmp_path, source, annotations, output)

    assert manifest["version"] == 3 and manifest["review_samples"] == 2
    assert manifest["train_samples"] == manifest["validation_samples"] == 2
    assert verification["operation_samples_unchanged"]
    assert {path.name: path.read_bytes() for path in source.glob("*.json*")} == source_bytes
    for partition in original:
        rows = read_rows(output / f"{partition}.jsonl")
        assert rows[0] == original[partition][0]
        assert rows[1]["review_format"] == "evidence_v1"
        assert rows[1]["scenario_fingerprint"] == partition
        assert json.loads(rows[1]["messages"][-1]["content"])["field_readings"][0]["input_value"] == "New"


def test_v3_requires_annotations_for_all_source_reviews(tmp_path):
    source, annotations = source_with_annotations(tmp_path)
    document = json.loads(annotations.read_text())
    document["annotations"].pop()
    write_json(annotations, document)
    with pytest.raises(ValueError, match="every source review"):
        build_dataset(tmp_path, source, annotations, tmp_path / "datasets/v3")
