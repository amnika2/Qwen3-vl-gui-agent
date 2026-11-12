import json
from pathlib import Path

import pytest
import yaml

from guiqa import doctor as diagnostic, tasks
from guiqa.artifacts import write_json
from guiqa.evaluate import evaluate


ROOT = Path(__file__).resolve().parents[1]


def test_dataset26_task_session_accepts_new_applications_without_legacy_loader(dataset26_package, monkeypatch):
    root = dataset26_package
    config = yaml.safe_load((ROOT / "configs/agent-dataset26-base.yaml").read_text())
    write_json(root / "configs/agent.json", config)
    task_file = root / "tasks.yaml"
    task_file.write_text(yaml.safe_dump({"config": "configs/agent.json", "tasks": [
        {"id": "auth", "case": "sweeper-087", "build": "A", "workflow": "target"},
        {"id": "validate", "case": "sweeper-151", "build": "B", "workflow": "target"}]}))

    def legacy_loader(*args):
        raise AssertionError("new applications must use the frozen dataset26 plan")

    monkeypatch.setattr(tasks, "load_case", legacy_loader)
    _, session = tasks.create_session(root, task_file)
    assert [row["case"] for row in session["tasks"]] == ["sweeper-087", "sweeper-151"]
    assert session["config"]["dataset"] == "datasets/dataset26-v1"


def test_dataset26_tasks_reject_an_application_outside_the_frozen_plan(dataset26_package):
    specification = {"tasks": [{"id": "legacy", "case": "sweeper-041", "build": "B", "workflow": "target"}]}
    with pytest.raises(ValueError, match="outside the frozen dataset26 plan"):
        tasks.validate_tasks(dataset26_package, specification, {"dataset": "datasets/dataset26-v1"})


@pytest.mark.parametrize("config,workflows,expected", [
    ({"dataset": "datasets/dataset26-v1"}, None, ["target"]),
    ({}, None, ["target", "preservation"]),
    ({"dataset": "datasets/dataset26-v1"}, ["preservation"], ["preservation"]),
])
def test_evaluation_schedule_uses_the_selected_dataset_workflows(tmp_path, monkeypatch, config, workflows, expected):
    def run_case(root, **cell):
        name = cell["build"] + "-" + cell["workflow"]
        directory = root / "runs" / name
        verdict = "fail" if cell["build"] == "A" and cell["workflow"] == "target" else "pass"
        result = {"run_id": name, "agent_verdict": verdict, "oracle_verdict": verdict,
                  "termination_reason": "script_completed", "elapsed_seconds": 1, "actions": 1, "model_calls": 0}
        write_json(directory / "result.json", result)
        return directory, result

    monkeypatch.setattr("guiqa.evaluate.run_case", run_case)
    suite = {"cases": ["sweeper-087"], "repeats": 1}
    if workflows is not None:
        suite["workflows"] = workflows
    directory, _ = evaluate(tmp_path, suite, ["script"], config)
    planned = json.loads((directory / "schedule.json").read_text())["planned"]
    assert len(planned) == 2 * len(expected)
    assert sorted({row["workflow"] for row in planned}) == sorted(expected)


def test_business_review_requires_schema_dependency_without_agent_runtime(monkeypatch):
    monkeypatch.setattr(diagnostic, "imports_check", lambda names: {"ok": True, "modules": names})
    result = diagnostic.model_dependencies_check({"review_format": "business_evidence_v1"})
    assert "lmformatenforcer.integrations.transformers" in result["modules"]


def test_dataset26_doctor_checks_metadata_and_execution_imports_without_starting_apps(dataset26_package, monkeypatch):
    directory = dataset26_package / "datasets/dataset26-v1"
    imported = []
    monkeypatch.setattr(diagnostic, "imports_check", lambda names: imported.extend(names) or {"ok": True})
    result = diagnostic.dataset26_check(directory)
    assert result["ok"] and result["applications"] == 26
    assert result["plan_matches_manifest"]
    assert imported == ["guiqa.dataset26_execution", "guiqa.dataset26_oracle", "guiqa.business_review"]
    assert "applications are not started" in result["scope"]
