import json
from pathlib import Path

import pytest
import yaml

from guiqa.rl_review_diagnostic import read_rows, validate_training_reviews
from guiqa.train_review_grpo import ROOT
from guiqa.train_review_grpo_pilot import last_checkpoint, select_rows


def test_pilot_curriculum_keeps_all_mechanisms_in_the_training_partition():
    settings = yaml.safe_load((ROOT / "configs/rl-review-grpo-pilot.yaml").read_text())
    dataset = ROOT / settings["dataset"]
    rows = validate_training_reviews(read_rows(dataset / "train.jsonl"), read_rows(dataset / "validation.jsonl"),
                                     yaml.safe_load((ROOT / "configs/sft-split.yaml").read_text()))
    selected = select_rows(rows, settings["source_run_ids"])
    gold = [json.loads(row["messages"][-1]["content"]) for row in selected]
    assert len(selected) == 32
    assert {label: sum(answer["verdict"] == label for answer in gold)
            for label in ("pass", "fail", "blocked")} == {"pass": 12, "fail": 10, "blocked": 10}
    assert len({row["case_id"] for row in selected}) == 10
    assert any("input_not_established" in name for name in settings["source_run_ids"])
    assert any("cancelled" in name for name in settings["source_run_ids"])
    assert any(answer["expected_rejection"] for answer in gold)


@pytest.mark.parametrize("run_ids", [["missing"], ["run", "run"]])
def test_curriculum_cannot_silently_drop_unknown_or_duplicate_runs(run_ids):
    with pytest.raises(ValueError):
        select_rows([{"derived_provenance": {"source_run_id": "run"}}], run_ids)


def test_resume_uses_a_fully_saved_checkpoint(tmp_path):
    complete = tmp_path / "checkpoint-10"
    complete.mkdir()
    (complete / "ready.json").write_text("{}")
    (tmp_path / "checkpoint-20").mkdir()
    assert last_checkpoint(tmp_path) == str(complete)
    assert last_checkpoint(tmp_path / "absent", required=False) is None
