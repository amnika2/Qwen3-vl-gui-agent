import json
from pathlib import Path
import sqlite3

from guiqa.artifacts import write_json
from guiqa.agent_memory import ExperienceStore
from guiqa import e2e_experiment as experiment


def test_worker_keeps_first_results_and_isolates_each_memory_copy(tmp_path, monkeypatch):
    seed = tmp_path / "memory-seed.sqlite3"
    ExperienceStore(seed).save("e2e-frozen", "training", "target", "Edit and save a record",
                               ["Save and reopen"], [], "training-run")
    config = {"model_path": "model", "agent_runtime": {"enabled": True, "memory": True}}
    group = {"id": "full", "config": config}
    cells = [{"id": str(index), "group": "full", "case_id": "test", "build": "B", "workflow": "target"}
             for index in range(3)]
    write_json(tmp_path / "protocol.json", {"groups": [group], "cells": cells})
    write_json(tmp_path / "cells/0.json", {"original": "blocked result is retained"})
    model = type("Model", (), {"load": lambda self: None, "begin_run": lambda self: None})()
    monkeypatch.setattr(experiment, "load_model", lambda *args: model)
    seen = []

    def run_case(root, case, build, workflow, method, selected_config, **kwargs):
        database = Path(selected_config["agent_runtime"]["memory_database"])
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT COUNT(*) FROM experiences").fetchone()[0] == 1
        seen.append(database)
        ExperienceStore(database).save("e2e-frozen", "test", "target", "Current task", [], [], database.stem)
        kwargs["on_started"](tmp_path / ("run-" + database.stem))
        return tmp_path / ("run-" + database.stem), {
            "agent_verdict": "pass", "oracle_verdict": "pass", "actions": 1,
            "elapsed_seconds": 1, "termination_reason": "controlled_review"}

    monkeypatch.setattr(experiment, "run_case", run_case)
    assert experiment.run_worker(tmp_path, tmp_path, 0, 1, 12345) == 0
    assert len(seen) == len(set(seen)) == 2
    assert json.loads((tmp_path / "cells/0.json").read_text()) == {"original": "blocked result is retained"}
    with sqlite3.connect(seed) as connection:
        assert connection.execute("SELECT COUNT(*) FROM experiences").fetchone()[0] == 1
    assert experiment.run_worker(tmp_path, tmp_path, 0, 1, 12345) == 0
    assert len(seen) == 2


def test_no_memory_worker_does_not_copy_a_database(tmp_path, monkeypatch):
    config = {"model_path": "model", "agent_runtime": {"enabled": True, "memory": False}}
    cell = {"id": "no-memory", "group": "no-memory", "case_id": "test", "build": "A", "workflow": "target"}
    write_json(tmp_path / "protocol.json", {"groups": [{"id": "no-memory", "config": config}], "cells": [cell]})
    model = type("Model", (), {"load": lambda self: None, "begin_run": lambda self: None})()
    monkeypatch.setattr(experiment, "load_model", lambda *args: model)

    def run_case(*args, **kwargs):
        assert not Path(args[5]["agent_runtime"]["memory_database"]).exists()
        return tmp_path / "run", {"agent_verdict": "blocked", "oracle_verdict": "inconclusive",
                                  "actions": 1, "elapsed_seconds": 1, "termination_reason": "action_budget"}

    monkeypatch.setattr(experiment, "run_case", run_case)
    assert experiment.run_worker(tmp_path, tmp_path, 0, 1, 12345) == 0
    assert not (tmp_path / "memory").exists()
