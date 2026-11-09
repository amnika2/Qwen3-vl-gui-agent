import json

import pytest
import yaml

from guiqa.artifacts import write_json
from guiqa.sft_export import export


def training_batch(root, case="sweeper-150", review_verdict="fail"):
    (root / "configs").mkdir()
    (root / "configs/sft-split.yaml").write_text(yaml.safe_dump({
        "eval_holdout": ["sweeper-040"], "training_pilot": ["sweeper-150"], "training_pool": [], "deferred": {"sweeper-149": "paused"}}))
    write_json(root / "runs/batch/schedule.json", {"suite": {"cases": [case]}})
    write_json(root / "runs/batch/completed.json", [{"path": "runs/one"}])
    write_json(root / "runs/one/result.json", {"run_id": "one", "case_id": case, "method": "sft", "build": "B",
        "workflow": "target", "actions": 3, "agent_verdict": review_verdict, "oracle_verdict": "fail"})
    sample = {"phase": "review", "messages": [{"role": "user", "content": "Check saved value"}], "label_source": "observed_run_v2",
              "assistant": {"action": "finish", "verdict": review_verdict, "observation": "Saved value is old", "evidence_steps": [3],
                            "requested_value_entered": True, "submission_succeeded": True, "final_record_visible": True}}
    path = root / "runs/one/sft/samples.jsonl"
    path.parent.mkdir()
    path.write_text(json.dumps(sample) + "\n")


def test_export_keeps_observed_build_b_failure(tmp_path):
    training_batch(tmp_path)
    output = tmp_path / "dataset"
    result = export(tmp_path, ["batch"], output)
    row = json.loads((output / "train.jsonl").read_text())
    assert result["samples"] == 1
    assert result["cases"] == ["sweeper-150"]
    assert not result["training_started"]
    assert json.loads(row["messages"][-1]["content"])["verdict"] == "fail"


@pytest.mark.parametrize("case", ["sweeper-040", "sweeper-149"])
def test_holdout_and_deferred_cases_cannot_be_exported(tmp_path, case):
    training_batch(tmp_path, case=case)
    with pytest.raises(ValueError, match="outside active training split"):
        export(tmp_path, ["batch"], tmp_path / "dataset")
    assert not (tmp_path / "dataset").exists()


def test_pass_label_on_observed_failure_is_rejected(tmp_path):
    training_batch(tmp_path, review_verdict="pass")
    with pytest.raises(ValueError, match="contradicts observed business outcome"):
        export(tmp_path, ["batch"], tmp_path / "dataset")


def test_supplementary_cases_are_marked_and_counted(tmp_path):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/sft-split.yaml").write_text(yaml.safe_dump({
        "eval_holdout": ["sweeper-040"], "training_pilot": ["sweeper-150"],
        "training_supplementary": ["sweeper-034"], "training_pool": [],
        "deferred": {"sweeper-149": "paused"}}))
    write_json(tmp_path / "runs/batch/completed.json", [{"path": "runs/one"}])
    write_json(tmp_path / "runs/one/result.json", {"run_id": "one", "case_id": "sweeper-034", "method": "sft",
        "build": "A", "workflow": "target", "actions": 2, "agent_verdict": "pass", "oracle_verdict": "pass"})
    rows = [
        {"phase": "operation", "messages": [{"role": "user", "content": "step 0"}],
         "assistant": {"action": "click", "coordinate": [100, 200]}},
        {"phase": "operation", "messages": [{"role": "user", "content": "step 1"}],
         "assistant": {"action": "type", "text": "GUIQA edited"}},
        {"phase": "review", "messages": [{"role": "user", "content": "review"}],
         "label_source": "observed_run_v2",
         "assistant": {"action": "finish", "verdict": "pass", "observation": "saved", "evidence_steps": [2],
                       "requested_value_entered": True, "submission_succeeded": True, "final_record_visible": True}},
    ]
    path = tmp_path / "runs/one/sft/samples.jsonl"
    path.parent.mkdir()
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    result = export(tmp_path, ["batch"], tmp_path / "dataset")
    assert result["apps"] == 1
    assert result["trajectories"] == 1
    assert result["operation_samples"] == 2
    assert result["review_samples"] == 1
    assert result["samples"] == 3
    assert result["case_source"] == {"sweeper-034": "supplementary"}
    assert result["samples_by_app"] == {"sweeper-034": 3}
    assert result["trajectories_by_app"] == {"sweeper-034": 1}
    assert result["accepted_runs"][0]["source"] == "supplementary"


def test_scenario_deduplication_prevents_cross_partition_leakage(tmp_path):
    training_batch(tmp_path)
    first = tmp_path / "runs/one/result.json"
    result = json.loads(first.read_text())
    result.update(scenario_id="one", scenario_family="replace", scenario_fingerprint="same-task", data_split="train")
    first.write_text(json.dumps(result))
    source = tmp_path / "runs/one/sft/samples.jsonl"
    second = tmp_path / "runs/two"
    (second / "sft").mkdir(parents=True)
    (second / "sft/samples.jsonl").write_bytes(source.read_bytes())
    (second / "result.json").write_text(json.dumps({**result, "run_id": "two", "scenario_id": "two", "data_split": "validation"}))
    write_json(tmp_path / "runs/batch/completed.json", [
        {"path": "runs/one", "matrix_matches": True}, {"path": "runs/two", "matrix_matches": True}])
    exported = export(tmp_path, ["batch"], tmp_path / "dataset")
    assert exported["trajectories"] == 1
    assert exported["validation_samples"] == 0
    assert exported["excluded_runs"] == [{"run_id": "two", "reason": "duplicate_scenario"}]


def test_mixed_legacy_and_scenario_runs_keep_whole_validation_trajectory(tmp_path):
    training_batch(tmp_path)
    first = tmp_path / "runs/one/result.json"
    result = json.loads(first.read_text())
    second = tmp_path / "runs/two"
    (second / "sft").mkdir(parents=True)
    (second / "sft/samples.jsonl").write_bytes((tmp_path / "runs/one/sft/samples.jsonl").read_bytes())
    (second / "result.json").write_text(json.dumps({**result, "run_id": "two", "scenario_id": "validation-task",
        "scenario_family": "append", "scenario_fingerprint": "different-task", "data_split": "validation"}))
    write_json(tmp_path / "runs/batch/completed.json", [
        {"path": "runs/one"}, {"path": "runs/two", "matrix_matches": True}])
    exported = export(tmp_path, ["batch"], tmp_path / "dataset")
    assert exported["train_trajectories"] == exported["validation_trajectories"] == 1
    assert len((tmp_path / "dataset/train.jsonl").read_text().splitlines()) == 1
    assert len((tmp_path / "dataset/validation.jsonl").read_text().splitlines()) == 1


def test_bundled_images_are_relative_and_keep_original_bytes(tmp_path):
    from PIL import Image

    training_batch(tmp_path)
    screenshot = tmp_path / "screen.png"
    Image.new("RGB", (16, 16), "blue").save(screenshot)
    source = tmp_path / "runs/one/sft/samples.jsonl"
    row = json.loads(source.read_text())
    row["messages"] = [{"role": "user", "content": [
        {"type": "image", "image": str(screenshot)}, {"type": "image", "image": str(screenshot)}]}]
    source.write_text(json.dumps(row) + "\n")
    output = tmp_path / "dataset"
    exported = export(tmp_path, ["batch"], output, bundle_images=True)
    sample = json.loads((output / "train.jsonl").read_text())
    paths = [item["image"] for item in sample["messages"][0]["content"]]
    assert paths[0] == paths[1] and paths[0].startswith("images/")
    assert (output / paths[0]).read_bytes() == screenshot.read_bytes()
    assert exported["images"] == 1 and exported["image_path_format"] == "relative_to_dataset"


def test_rejected_submission_fail_cannot_use_persistence_profile(tmp_path):
    training_batch(tmp_path)
    source = tmp_path / "runs/one/sft/samples.jsonl"
    row = json.loads(source.read_text())
    row["assistant"].update(submission_succeeded=False, final_record_visible=False)
    source.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="contradicts prompt profile"):
        export(tmp_path, ["batch"], tmp_path / "dataset")
