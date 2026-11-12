import json

import pytest

from guiqa import dataset26_training


@pytest.fixture
def training_boundary():
    applications = {"train-app": {"category": "F", "base_commit": "1" * 40,
                                  "reference_commit": "2" * 40}}
    row = {"id": "train-app-A-op-0", "case_id": "train-app", "split": "train",
           "category": "F", "build": "A", "source_commit": "1" * 40}
    return applications, row


def test_training_loader_reads_only_train_and_validation(tmp_path, monkeypatch):
    for split in ("train", "validation"):
        (tmp_path / (split + ".jsonl")).write_text(json.dumps({"split": split}) + "\n")
    seen = []
    monkeypatch.setattr(dataset26_training, "validate_dataset26",
                        lambda root, partitions: seen.extend(partitions) or {"passed": True})
    train, validation, checked = dataset26_training.load_training_partitions(tmp_path, "dataset26-v1")
    assert train == [{"split": "train"}]
    assert validation == [{"split": "validation"}]
    assert seen == ["train", "validation"]
    assert checked == {"passed": True}


def test_training_boundary_rejects_validation_application(training_boundary):
    applications, row = training_boundary
    row.update(case_id="validation-app", split="validation")
    with pytest.raises(ValueError, match="crosses its application split"):
        dataset26_training.validate_partition([row], "train", applications, set(), set(), set())


def test_training_boundary_rejects_changed_source_commit(training_boundary):
    applications, row = training_boundary
    row["source_commit"] = "0" * 40
    with pytest.raises(ValueError, match="source differs from its frozen build"):
        dataset26_training.validate_partition([row], "train", applications, set(), set(), set())
