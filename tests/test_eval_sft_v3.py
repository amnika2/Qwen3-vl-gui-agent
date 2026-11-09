import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from evaluate_sft_v3 import model_record, prepare, report
from guiqa.artifacts import write_json


def evaluation_inputs(root):
    (root / "configs").mkdir()
    config = {"model_path": "vendor/model", "model_revision": "pinned", "review_format": "evidence_v1",
              "max_actions": 30, "wall_seconds": 600, "max_new_tokens": 384,
              "review_max_new_tokens": 768, "max_pixels": 802816, "seed": 42, "history_actions": 8}
    (root / "configs/agent.yaml").write_text(yaml.safe_dump(config))
    suite = {"agent_config": "configs/agent.yaml", "method": "qwen-controlled",
             "cases": ["sweeper-005"], "builds": ["A", "B"],
             "workflows": ["target", "preservation"], "repeats": 2, "models": {}}
    for variant in ("v2", "v3"):
        directory = root / "runs" / variant / "adapter"
        directory.mkdir(parents=True)
        weight = directory / "adapter_model.safetensors"
        weight.write_bytes(variant.encode())
        write_json(directory.parent / "completion.json", {"completed": True, "selected_epoch": 2,
                   "adapter_sha256": hashlib.sha256(weight.read_bytes()).hexdigest()})
        suite["models"][variant] = str(directory.relative_to(root))
    return suite, SimpleNamespace(experiment="eval", command="run"), {"sha256": "same-core", "files": {}}


def test_both_variants_load_their_selected_adapter_with_identical_budgets(tmp_path):
    suite, args, core = evaluation_inputs(tmp_path)
    schedules = []
    for variant in ("v2", "v3"):
        selected = model_record(tmp_path, suite, variant)
        _, schedule, pending = prepare(tmp_path, args, suite, variant, "sweeper-005", core, selected)
        assert len(pending) == 8
        assert schedule["config"]["adapter_path"].endswith(f"/{variant}/adapter")
        assert schedule["config"]["adapter_sha256"] == selected["adapter_sha256"]
        schedules.append(schedule)
    configs = [{key: value for key, value in schedule["config"].items()
                if key not in {"adapter_path", "adapter_sha256"}} for schedule in schedules]
    assert configs[0] == configs[1]
    assert configs[0]["review_format"] == "evidence_v1"
    assert configs[0]["max_new_tokens"] == 384 and configs[0]["review_max_new_tokens"] == 768


def test_resume_keeps_failures_and_only_resumes_interrupted_cells(tmp_path):
    suite, args, core = evaluation_inputs(tmp_path)
    selected = model_record(tmp_path, suite, "v2")
    directory, schedule, planned = prepare(tmp_path, args, suite, "v2", "sweeper-005", core, selected)
    write_json(directory / "schedule.json", schedule)
    write_json(directory / "completed.json", [
        {**planned[0], "termination_reason": "infrastructure_failure"},
        {**planned[1], "termination_reason": "interrupted"}])
    _, _, pending = prepare(tmp_path, args, suite, "v2", "sweeper-005", core, selected)
    assert len(pending) == 7
    assert planned[0] not in pending and planned[1] in pending


def test_paired_report_preserves_blocked_and_schema_errors_in_denominators(tmp_path):
    suite, args, core = evaluation_inputs(tmp_path)
    for variant in ("v2", "v3"):
        selected = model_record(tmp_path, suite, variant)
        directory, schedule, planned = prepare(tmp_path, args, suite, variant, "sweeper-005", core, selected)
        write_json(directory / "schedule.json", schedule)
        completed = []
        for index, cell in enumerate(planned):
            verdict = "fail" if cell["build"] == "A" and cell["workflow"] == "target" else "pass"
            result = {"run_id": f"{variant}-{index}", "agent_verdict": verdict, "oracle_verdict": verdict,
                      "actions": 6, "model_calls": 7, "termination_reason": "controlled_review",
                      "control_trigger": "model_proposed_finish", "field_readings": [
                          {"input_value": "New", "observed_value": "Old" if verdict == "fail" else "New"}],
                      "review_images": 3, "submission_step": 4}
            if variant == "v2" and index in (0, 7):
                result["agent_verdict"] = "blocked"
                if index == 0:
                    result["termination_reason"] = "review_invalid_output"
                    result.pop("field_readings")
            path = Path("runs") / result["run_id"]
            write_json(tmp_path / path / "result.json", result)
            completed.append({**cell, "variant": variant, "path": str(path),
                              "termination_reason": result["termination_reason"]})
        write_json(directory / "completed.json", completed)
    output = report(tmp_path, "eval", suite)
    comparison = json.loads((output / "comparison.json").read_text())
    left, right = comparison["metrics"]["v2"], comparison["metrics"]["v3"]
    assert left["runs"] == right["runs"] == 8
    assert left["correct_cells"] == 6 and right["correct_cells"] == 8
    assert left["detected"] == 1 and left["target_runs"] == 2
    assert left["normal_correct"] == 5 and left["normal_runs"] == 6
    assert left["agent_blocked"] == 2 and left["review_invalid_output"] == 1
    assert right["correct_target_pairs"] == right["correct_four_cell_sets"] == 2
    assert right["active_finish_correct"] == 8
    assert comparison["all_worker_inference_core_identical"]
    assert comparison["inference_core_pairs"] == {"sweeper-005": True}
    assert "v2和v3" in (output / "summary.md").read_text()
