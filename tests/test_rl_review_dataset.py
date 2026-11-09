import copy
import shutil
from pathlib import Path

import pytest
import yaml

from guiqa.rl_review_dataset import _validate_reviews, build_dataset
from guiqa.sft_v2 import checksum, read_json, read_rows, write_json, write_rows


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "datasets/sft-main-10apps-v4"
WOLF = ROOT / "runs/rl-targeted-wolf-v1/reviews.jsonl"


def test_real_wolf_merge_keeps_frozen_gold_partitions_and_original_bytes(tmp_path):
    names = ("manifest.json", "train.jsonl", "validation.jsonl", "media_manifest.json")
    before = {name: checksum(SOURCE / name) for name in names}
    output = tmp_path / "merged"
    manifest, verification = build_dataset(ROOT, SOURCE, [WOLF], output)

    assert verification["passed"]
    assert (manifest["samples"], manifest["train_samples"], manifest["validation_samples"]) == (144, 115, 29)
    assert manifest["curriculum_review_samples"] == 14
    for partition in ("train", "validation"):
        bundled = {row["id"]: row for row in read_rows(output / f"{partition}.jsonl")}
        for original in read_rows(SOURCE / f"{partition}.jsonl"):
            if original["phase"] == "review":
                saved = bundled[original["id"]]
                assert saved["messages"][-1] == original["messages"][-1]
                for key in ("data_split", "scenario_id", "scenario_fingerprint", "derived_provenance"):
                    assert saved[key] == original[key]
    assert {name: checksum(SOURCE / name) for name in names} == before
    for relative, image in read_json(output / "media_manifest.json").items():
        assert checksum(output / relative) == image["sha256"]


def test_new_only_preserves_pending_review_status_and_does_not_export_old_rows(tmp_path):
    manifest, _ = build_dataset(ROOT, SOURCE, [WOLF], tmp_path / "new", new_only=True)
    assert manifest["source_review_samples"] == 0
    assert manifest["samples"] == 14
    assert manifest["pixel_review"]["new_sample_status_counts"] == {"pending": 14}
    assert manifest["review_labels"] == {"fail": 4, "pass": 10}
    rows = read_rows(tmp_path / "new/train.jsonl") + read_rows(tmp_path / "new/validation.jsonl")
    assert all(row["derived_provenance"]["human_pixel_review"] == "pending" for row in rows)


def copied_wolf_batch(tmp_path):
    row = copy.deepcopy(read_rows(WOLF)[0])
    batch = tmp_path / "curriculum"
    batch.mkdir()
    directory = batch / row["id"]
    shutil.copytree(Path(row["run_directory"]), directory)
    row["run_directory"] = str(directory)
    row["derived_provenance"]["deployment_manifest"] = str(directory / "private/instance/instance.json")
    write_rows(batch / "reviews.jsonl", [row])
    result = read_json(directory / "result.json")
    manifest = {"complete": True, "calibration": False, "samples_planned": 1,
                "completed": [result], "dataset_sha256": {"reviews.jsonl": checksum(batch / "reviews.jsonl")}}
    write_json(batch / "manifest.json", manifest)
    return batch, row, manifest


def test_incomplete_collection_is_rejected_before_creating_dataset(tmp_path):
    batch, _, manifest = copied_wolf_batch(tmp_path)
    manifest["complete"] = False
    write_json(batch / "manifest.json", manifest)
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="complete and non-calibration"):
        build_dataset(ROOT, SOURCE, [batch / "reviews.jsonl"], output)
    assert not output.exists()


@pytest.mark.parametrize("artifact", ["trace.jsonl", "screenshots/003.png"])
def test_modified_actual_trace_or_selected_pixels_are_rejected(tmp_path, artifact):
    batch, row, _ = copied_wolf_batch(tmp_path)
    path = Path(row["run_directory"]) / artifact
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="source artifact changed"):
        build_dataset(ROOT, SOURCE, [batch / "reviews.jsonl"], tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_consistently_rehashed_wrong_deployment_commit_is_still_rejected(tmp_path):
    batch, row, manifest = copied_wolf_batch(tmp_path)
    directory = Path(row["run_directory"])
    deployment = directory / "private/instance/instance.json"
    instance = read_json(deployment)
    instance["commit"] = "0" * 40
    write_json(deployment, instance)
    sha = checksum(deployment)
    row["derived_provenance"]["deployment_manifest_sha256"] = sha
    write_rows(batch / "reviews.jsonl", [row])
    result = manifest["completed"][0]
    result["source_hashes"]["private/instance/instance.json"] = sha
    write_json(directory / "result.json", result)
    manifest["dataset_sha256"]["reviews.jsonl"] = checksum(batch / "reviews.jsonl")
    write_json(batch / "manifest.json", manifest)
    with pytest.raises(ValueError, match="deployment commit differs"):
        build_dataset(ROOT, SOURCE, [batch / "reviews.jsonl"], tmp_path / "output")


@pytest.mark.parametrize("key", ["scenario_id", "scenario_fingerprint", "source_run_id"])
def test_a_relabelled_partition_cannot_reuse_a_training_scenario(key):
    original = next(row for row in read_rows(SOURCE / "train.jsonl") if row["phase"] == "review")
    other = copy.deepcopy(original)
    other.update(id="new-validation-review", data_split="validation",
                 scenario_id="new-validation-scenario", scenario_fingerprint="new-validation-fingerprint")
    other["derived_provenance"]["source_run_id"] = "new-validation-run"
    if key == "source_run_id":
        other["derived_provenance"][key] = original["derived_provenance"][key]
    else:
        other[key] = original[key]
    split = yaml.safe_load((ROOT / "configs/sft-split.yaml").read_text())
    with pytest.raises(ValueError, match="overlap on " + key):
        _validate_reviews([original, other], split)
