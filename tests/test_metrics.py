from guiqa.artifacts import write_json
from guiqa.evaluate import summarize


def test_unstarted_schedule_entries_remain_in_denominator(tmp_path):
    directory = tmp_path / "runs/batch"
    base = {"case_id": "sweeper-041", "method": "qwen-base", "workflow": "target", "repeat": 0}
    planned = [{**base, "build": "A"}, {**base, "build": "B"}]
    write_json(directory / "schedule.json", {"experiment": "batch", "planned": planned})
    write_json(directory / "completed.json", [{**planned[0], "path": "runs/first"}])
    write_json(tmp_path / "runs/first/result.json", {"run_id": "first", "agent_verdict": "fail", "oracle_verdict": "fail",
               "elapsed_seconds": 5, "actions": 3, "model_calls": 4, "termination_reason": "model_finished"})
    report = summarize(tmp_path, directory)
    summary = (report / "summary.md").read_text()
    assert "计划 2 次；已启动 1 次" in summary
    assert "1/1 | 0/1 | 0/2 | 0/2 | 0/1" in summary


def test_matrix_rejects_agent_false_alarms_even_when_product_state_is_correct(tmp_path, monkeypatch):
    from guiqa.evaluate import evaluate
    import json

    def fake_run(root, **cell):
        name = f"{cell['build']}-{cell['workflow']}"
        path = root / "runs" / name
        expected = "fail" if cell["build"] == "A" and cell["workflow"] == "target" else "pass"
        result = {"run_id": name, "oracle_verdict": expected, "agent_verdict": "fail", "elapsed_seconds": 1,
                  "actions": 2, "model_calls": 3, "termination_reason": "model_finished"}
        write_json(path / "result.json", result)
        return path, result
    monkeypatch.setattr("guiqa.evaluate.run_case", fake_run)
    directory, _ = evaluate(tmp_path, {"cases": ["sweeper-041"], "repeats": 1}, ["qwen-base"], {})
    matrix = json.loads((directory / "matrix.json").read_text())
    assert not matrix["passed"]
    assert sum(item["matrix_matches"] for item in matrix["cells"]) == 1
