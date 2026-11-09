import csv
import json

from guiqa.artifacts import write_json
from guiqa.e2e_report import summarize


GROUPS = ["base-full", "sft-full", "grpo-full", "grpo-no-planning", "grpo-no-memory", "grpo-no-reflection"]


def protocol(tmp_path):
    directory = tmp_path / "experiments/demo"
    groups = []
    cells = []
    for group in GROUPS:
        runtime = {"enabled": True}
        if "-no-" in group:
            runtime[group.split("-no-")[1]] = False
        groups.append({"id": group, "config": {"agent_runtime": runtime}})
        cells.extend({"id": group + "-" + build, "group": group, "case_id": "sweeper-008",
                      "build": build, "workflow": "target"} for build in ("A", "B"))
    write_json(directory / "protocol.json", {"id": "demo", "groups": groups, "cells": cells})
    return directory, cells


def save_result(directory, cell, agent, oracle, *, actions=5, usage=True):
    result = {"run_id": cell["id"] + "-first", "matrix_expected": "fail" if cell["build"] == "A" else "pass",
              "agent_verdict": agent, "oracle_verdict": oracle, "actions": actions, "model_calls": actions + 2,
              "elapsed_seconds": 10, "termination_reason": "infrastructure_failure" if oracle == "infra_error" else "controlled_review"}
    if usage:
        result["gpu"] = {"prompt_tokens": 200, "completion_tokens": 50, "generation_seconds": 7}
    write_json(directory / "cells" / (cell["id"] + ".json"), {**cell, "run_dir": "/runs/" + result["run_id"], "result": result})


def test_defect_success_and_failed_or_missing_runs_stay_in_denominator(tmp_path):
    directory, cells = protocol(tmp_path)
    save_result(directory, cells[0], "fail", "fail")
    save_result(directory, cells[1], "blocked", "inconclusive")
    save_result(directory, cells[2], "blocked", "infra_error")
    save_result(directory, {**cells[1], "id": cells[1]["id"] + "-retry"}, "pass", "pass")
    summary = summarize(tmp_path, directory)
    assert summary["planned"] == 12
    assert summary["started"] == 3
    assert summary["not_started"] == 9
    assert summary["groups"]["base-full"]["e2e_successes"] == 1
    assert summary["groups"]["base-full"]["e2e_success_rate"] == 0.5
    assert summary["groups"]["base-full"]["execution_completed"] == 1
    assert summary["groups"]["sft-full"]["planned"] == 2
    assert summary["groups"]["sft-full"]["infra_errors"] == 1
    assert summary["groups"]["sft-full"]["not_started"] == 1
    assert summary["groups"]["sft-full"]["e2e_success_rate"] == 0
    with (directory / "results.csv").open() as file:
        rows = list(csv.DictReader(file))
    assert rows[0]["e2e_success"] == "True"
    assert rows[3]["termination_reason"] == "not_started"
    assert "正确检出缺陷并输出 fail" in (directory / "summary.md").read_text()
    assert json.loads((directory / "summary.json").read_text()) == summary


def test_full_baseline_is_reused_and_pair_differences_match_tasks(tmp_path):
    directory, cells = protocol(tmp_path)
    for cell in cells:
        expected = "fail" if cell["build"] == "A" else "pass"
        if cell["group"] == "grpo-no-memory" and cell["build"] == "B":
            save_result(directory, cell, "blocked", "inconclusive", actions=9)
        else:
            save_result(directory, cell, expected, expected, actions=5)
    summary = summarize(tmp_path, directory)
    assert summary["started"] == 12
    assert summary["groups"]["grpo-full"]["planned"] == 2
    assert len(summary["ablations"]) == 3
    comparison = next(item for item in summary["ablations"] if item["reference"] == "grpo-no-memory")
    assert comparison["e2e_rate_difference_pp"] == 50
    assert comparison["pairs_observed"] == 2
    assert (comparison["wins"], comparison["losses"], comparison["ties"]) == (1, 0, 1)
    with (directory / "paired-differences.csv").open() as file:
        pairs = list(csv.DictReader(file))
    pair = next(row for row in pairs if row["reference"] == "grpo-no-memory" and row["build"] == "B")
    assert pair["group_run_id"] == "grpo-full-B-first"
    assert pair["e2e_success_delta"] == "1"
    assert pair["actions_delta"] == "-4"
    assert len(list((directory / "cells").glob("*.json"))) == 12


def test_usage_missing_is_explicit_and_ablation_switches_are_reported(tmp_path):
    directory, cells = protocol(tmp_path)
    for cell in cells:
        expected = "fail" if cell["build"] == "A" else "pass"
        save_result(directory, cell, expected, expected, usage=cell["group"] != "grpo-no-planning")
    summary = summarize(tmp_path, directory)
    assert summary["groups"]["base-full"]["prompt_tokens"] == 400
    assert summary["groups"]["base-full"]["completion_tokens"] == 100
    missing = summary["groups"]["grpo-no-planning"]
    assert missing["usage_missing"] == 2
    assert missing["completion_tokens"] == 0
    assert missing["mean_generation_seconds"] is None
    with (directory / "results.csv").open() as file:
        rows = list(csv.DictReader(file))
    planning = next(row for row in rows if row["group"] == "grpo-no-planning")
    assert planning["planning"] == "False"
    assert planning["memory"] == "True"
    assert planning["usage_recorded"] == "False"
    with (directory / "paired-differences.csv").open() as file:
        pairs = list(csv.DictReader(file))
    pair = next(row for row in pairs if row["reference"] == "grpo-no-planning")
    assert pair["completion_tokens_delta"] == ""
    assert pair["actions_delta"] == "0"
