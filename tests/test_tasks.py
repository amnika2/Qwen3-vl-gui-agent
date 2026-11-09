import json
from pathlib import Path

import pytest
import yaml

from guiqa.artifacts import write_json
from guiqa.cli import arguments, main
from guiqa import tasks


ROOT = Path(__file__).resolve().parents[1]


def make_session(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "load_case", lambda *args: ({}, {}))
    config = yaml.safe_load((ROOT / "configs/agent-full-base.yaml").read_text())
    write_json(tmp_path / "configs/agent.json", config)
    task_file = tmp_path / "tasks.yaml"
    task_file.write_text(yaml.safe_dump({"config": "configs/agent.json", "tasks": [
        {"id": "edit", "case": "demo", "build": "B", "workflow": "target"},
        {"id": "defect", "case": "demo", "build": "A", "workflow": "target"}]}))
    return tasks.create_session(tmp_path, task_file)


def test_task_session_resume_retains_results_and_restarts_only_interrupted(tmp_path, monkeypatch):
    directory, session = make_session(tmp_path, monkeypatch)
    calls = []
    outcomes = iter([("pass", "pass", "controlled_review"), ("blocked", "inconclusive", "interrupted"),
                     ("fail", "fail", "controlled_review")])

    def run_case(root, case, build, workflow, method, config, **kwargs):
        calls.append((build, kwargs["repeat"], config["agent_runtime"]["memory_database"]))
        verdict, oracle, reason = next(outcomes)
        run_id = f"attempt-{len(calls)}"
        run_dir = root / "runs" / run_id
        result = {"run_id": run_id, "matrix_expected": "fail" if build == "A" else "pass",
                  "agent_verdict": verdict, "oracle_verdict": oracle, "termination_reason": reason,
                  "actions": 4, "model_calls": 6, "elapsed_seconds": 2}
        write_json(run_dir / "result.json", result)
        kwargs["on_started"](run_dir)
        live = tasks.status(root, directory)
        assert live["active_run"]["result"]["run_id"] == run_id
        assert (directory / "report.md").exists()
        return run_dir, result

    monkeypatch.setattr(tasks, "run_case", run_case)
    tasks.run_tasks(tmp_path, directory, session, object())
    assert session["status"] == "interrupted"
    assert not session["successful"]
    restored = json.loads((directory / "session.json").read_text())
    tasks.run_tasks(tmp_path, directory, restored, object())
    assert restored["successful"]
    assert [row[:2] for row in calls] == [("B", 0), ("A", 0), ("A", 1)]
    assert len({row[2] for row in calls}) == 1
    assert calls[0][2] == str(directory / "memory.sqlite3")
    assert len(restored["tasks"][0]["attempts"]) == 1
    assert len(restored["tasks"][1]["attempts"]) == 2
    assert (tmp_path / "runs/attempt-2/result.json").exists()
    assert "attempt-3/report.md" in (directory / "report.md").read_text()
    assert tasks.status(tmp_path, directory)["tasks"][1]["attempts"] == 2


def test_retry_failed_keeps_original_attempt_and_skips_successful(tmp_path, monkeypatch):
    directory, session = make_session(tmp_path, monkeypatch)
    session["tasks"][0]["attempts"] = [{"successful": True, "termination_reason": "controlled_review",
                                          "run_id": "old-pass", "run_dir": str(tmp_path / "runs/old-pass"),
                                          "agent_verdict": "pass", "oracle_verdict": "pass"}]
    failed = {"successful": False, "termination_reason": "controlled_review_guarded",
              "run_id": "old-fail", "run_dir": str(tmp_path / "runs/old-fail"),
              "agent_verdict": "blocked", "oracle_verdict": "inconclusive"}
    session["tasks"][1]["attempts"] = [failed]
    calls = []

    def run_case(*args, **kwargs):
        calls.append(kwargs)
        return tmp_path / "runs/new", {"run_id": "new", "matrix_expected": "fail", "agent_verdict": "fail",
                                       "oracle_verdict": "fail", "termination_reason": "controlled_review",
                                       "actions": 5, "model_calls": 6, "elapsed_seconds": 3}

    monkeypatch.setattr(tasks, "run_case", run_case)
    tasks.run_tasks(tmp_path, directory, session, object())
    assert not calls
    tasks.run_tasks(tmp_path, directory, session, object(), retry_failed=True)
    assert len(calls) == 1
    assert session["tasks"][1]["attempts"][0] == failed
    assert session["successful"]


def test_invalid_tasks_are_rejected_before_session_creation(tmp_path):
    task_file = tmp_path / "tasks.yaml"
    task_file.write_text("tasks: [{id: example, case: fake, build: B}]\n")
    with pytest.raises(ValueError, match="exactly"):
        tasks.create_session(tmp_path, task_file)
    assert not (tmp_path / "runs").exists()


def test_status_cli_reads_live_state_without_model(tmp_path, capsys):
    directory = tmp_path / "runs/example"
    write_json(directory / "result.json", {"run_id": "example", "actions": 2})
    write_json(directory / "task-state.json", {"phase": "executing", "next_goal": "Save the record"})
    assert main(["--root", str(tmp_path), "status", "--run-dir", str(directory)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["task_state"]["next_goal"] == "Save the record"
    assert result["result"]["actions"] == 2


def test_tasks_cli_arguments_and_full_sft_config():
    args = arguments(["tasks", "--resume", "runs/session", "--retry-failed"])
    assert args.retry_failed
    assert args.resume == Path("runs/session")
    from guiqa.cli import load_config
    config = load_config(ROOT / "configs/agent-full-sft.yaml")
    assert config["agent_runtime"]["enabled"]
    assert config["adapter_path"].endswith("sft-qwen3vl-review-expanded-v1/adapter")


def test_resume_cannot_silently_replace_saved_config():
    with pytest.raises(SystemExit):
        arguments(["tasks", "--resume", "runs/session", "--config", "configs/agent-full-base.yaml"])


def test_task_config_type_is_validated_at_entry(tmp_path):
    with pytest.raises(ValueError, match="config must be a nonempty string"):
        tasks.validate_tasks(tmp_path, {"config": ["wrong"], "tasks": [{}]})


def test_invalid_yaml_is_reported_without_starting_a_task(tmp_path, capsys):
    task_file = tmp_path / "tasks.yaml"
    task_file.write_text("tasks: [invalid YAML\n")
    assert main(["--root", str(tmp_path), "tasks", "--task-file", str(task_file)]) == 3
    assert "guiqa:" in capsys.readouterr().out
    assert not (tmp_path / "runs").exists()
