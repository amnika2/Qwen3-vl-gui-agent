import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_dataset26_e2e as e2e


def test_frozen_schedule_is_exactly_sixteen_conditions(dataset26_package):
    conditions = e2e.frozen_conditions(dataset26_package)
    assert len(conditions) == 16
    assert len({row["instance_id"] for row in conditions}) == 8
    assert e2e.select_conditions(conditions, ["sweeper-087"]) == [
        row for row in conditions if row["case_id"] == "sweeper-087"]


def test_resume_preserves_saved_conditions_and_passes_frozen_task(tmp_path, monkeypatch, dataset26_package):
    conditions = e2e.frozen_conditions(dataset26_package)[:2]
    called = []

    def run_case(root, case_id, build, workflow, method, config, **kwargs):
        called.append(copy.deepcopy(kwargs["condition"]))
        config["changed_by_run"] = True
        result = {"oracle_verdict": "fail" if build == "A" else "pass",
                  "agent_verdict": "fail" if build == "A" else "pass",
                  "termination_reason": "script_completed", "model_calls": 0}
        return tmp_path / build, result

    monkeypatch.setattr(e2e, "run_case", run_case)
    config = {"dataset": "datasets/dataset26-v1"}
    first = e2e.run_conditions(dataset26_package, tmp_path / "output", conditions, config, "script", 14650)
    second = e2e.run_conditions(dataset26_package, tmp_path / "output", conditions, config, "script", 14650)
    assert called == conditions
    assert first == second
    assert first["mode"] == "oracle_calibration"
    assert first["agent_oracle_agreements"] is None
    assert first["model_evaluation_executed"] is False
    assert e2e.exit_code(first) == 0
    assert first["pairs"][0]["oracle_calibrated"] is True
    assert config == {"dataset": "datasets/dataset26-v1"}
    with pytest.raises(ValueError, match="different conditions"):
        e2e.run_conditions(dataset26_package, tmp_path / "output", conditions[:1], config, "script", 14650)


def test_model_summary_separates_invalid_oracle_from_agent_agreement(dataset26_package):
    conditions = e2e.frozen_conditions(dataset26_package)[:2]
    results = [{"condition": row, "result": {"oracle_verdict": oracle, "agent_verdict": agent,
                "termination_reason": "controlled_review", "model_calls": 2}}
               for row, oracle, agent in zip(conditions, ["inconclusive", "pass"], ["blocked", "pass"])]
    summary = e2e.summarize(conditions, results, "qwen-controlled")
    assert summary["oracle_valid"] == 1
    assert summary["oracle_inconclusive"] == 1
    assert summary["agent_oracle_agreements"] == 1
    assert summary["pairs"][0]["oracle_calibrated"] is False
    assert summary["model_evaluation_executed"] is True


def test_correct_oracles_with_wrong_model_verdicts_exit_unsuccessfully(dataset26_package):
    conditions = e2e.frozen_conditions(dataset26_package)[:2]
    results = [{"condition": row, "result": {"oracle_verdict": oracle, "agent_verdict": agent,
                "termination_reason": "controlled_review", "model_calls": 2}}
               for row, oracle, agent in zip(conditions, ["fail", "pass"], ["pass", "fail"])]
    summary = e2e.summarize(conditions, results, "qwen-controlled")
    assert summary["oracle_valid"] == summary["oracle_expected_matches"] == 2
    assert summary["agent_oracle_agreements"] == 0
    assert e2e.exit_code(summary) == 4
