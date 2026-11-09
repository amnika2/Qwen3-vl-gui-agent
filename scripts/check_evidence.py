import json
from pathlib import Path
from unittest.mock import patch

import yaml

from guiqa.artifacts import write_json
from guiqa.model import FakeModel
from guiqa.runner import run_case


def main():
    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / "configs/agent.yaml").read_text())
    finish_fail = {"action": "finish", "verdict": "fail", "observation": "Synthetic test verdict", "evidence_steps": [0]}
    finish_pass = {"action": "finish", "verdict": "pass", "observation": "Synthetic test verdict", "evidence_steps": [5]}
    sequence = [
        {"action": "click", "coordinate": [600 / 1279 * 1000, 401 / 899 * 1000]},
        {"action": "press", "key": "Control+a"}, {"action": "type", "text": "Saved Pen Name"},
        {"action": "click", "coordinate": [388 / 1279 * 1000, 718 / 899 * 1000]},
        {"action": "press", "key": "Control+r"}, finish_pass]
    scenarios = [
        ("unsaved_false_alarm", "A", [finish_fail], "inconclusive"),
        ("confirmed_regression", "A", sequence[:-1] + [{**finish_fail, "evidence_steps": [5]}], "fail"),
        ("fixed_false_alarm", "B", sequence[:-1] + [{**finish_fail, "evidence_steps": [5]}], "pass"),
    ]
    output = []
    for name, build, responses, expected in scenarios:
        directory, result = run_case(root, "sweeper-041", build, "target", "fake-smoke", config,
            model=FakeModel([json.dumps(value) for value in responses]), experiment="evidence-smoke", port=13221)
        item = {"scenario": name, "expected_oracle": expected, "actual": result["oracle_verdict"],
                "agent": result["agent_verdict"], "run_dir": str(directory.relative_to(root))}
        output.append(item)
        print(json.dumps(item), flush=True)
        if result["oracle_verdict"] != expected:
            raise AssertionError(item)
    with patch("guiqa.runner.sync_playwright", side_effect=RuntimeError("synthetic browser startup failure")):
        directory, result = run_case(root, "sweeper-041", "B", "target", "fake-smoke", config,
            model=FakeModel([]), experiment="evidence-smoke", port=13221)
    item = {"scenario": "browser_startup_failure", "expected_oracle": "infra_error",
            "actual": result["oracle_verdict"], "agent": result["agent_verdict"],
            "run_dir": str(directory.relative_to(root))}
    output.append(item)
    if result["oracle_verdict"] != "infra_error":
        raise AssertionError(item)
    write_json(root / "reports/evidence-smoke.json", {"synthetic_model": True, "real_application": True, "scenarios": output})


if __name__ == "__main__":
    main()
