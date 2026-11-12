"""Run the 16 frozen dataset26 E2E conditions with isolated application runs."""

import argparse
import copy
import json
from pathlib import Path

from guiqa.artifacts import source_digest, write_json
from guiqa.cli import load_config, load_model
from guiqa.dataset26_training import checksum, read_jsonl
from guiqa.runner import run_case


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--method", choices=("script", "qwen-controlled"), default="qwen-controlled")
    parser.add_argument("--case", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--config", type=Path, default=Path("configs/agent-dataset26-base.yaml"))
    parser.add_argument("--port", type=int, default=14650)
    parser.add_argument("--output", type=Path, default=Path("runs/dataset26-v1/e2e-evaluation"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    conditions = frozen_conditions(root)
    selected = select_conditions(conditions, args.case, args.limit)
    config = load_config(root / args.config)
    if config.get("dataset") != "datasets/dataset26-v1":
        raise ValueError("dataset26 E2E requires the dataset26 agent config")
    summary = run_conditions(root, root / args.output, selected, config, args.method, args.port)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return exit_code(summary)


def frozen_conditions(root):
    dataset = Path(root) / "datasets/dataset26-v1"
    manifest = json.loads((dataset / "manifest.json").read_text())
    path = dataset / "e2e/conditions.jsonl"
    if checksum(path) != manifest["e2e_conditions_sha256"]:
        raise ValueError("E2E conditions differ from the frozen dataset manifest")
    conditions = read_jsonl(path)
    applications = {app["case_id"]: app for app in
                    json.loads((dataset / "plan.json").read_text())["applications"] if app["split"] == "test"}
    expected = {(case_id, build) for case_id in applications for build in ("A", "B")}
    if len(conditions) != 16 or len({row["id"] for row in conditions}) != 16 or {
        (row["case_id"], row["build"]) for row in conditions
    } != expected:
        raise ValueError("dataset26 E2E requires exactly the eight test applications in both builds")
    for row in conditions:
        app = applications[row["case_id"]]
        commit = app["base_commit" if row["build"] == "A" else "reference_commit"]
        if row["source_commit"] != commit or row["category"] != app["category"]:
            raise ValueError("E2E application metadata differs from the frozen plan: " + row["id"])
    return conditions


def select_conditions(conditions, cases=None, limit=None):
    if cases and set(cases) - {row["case_id"] for row in conditions}:
        raise ValueError("--case must identify a dataset26 test application")
    if limit is not None and limit < 1:
        raise ValueError("--limit must be positive")
    selected = [row for row in conditions if not cases or row["case_id"] in cases]
    return selected[:limit]


def run_conditions(root, output, conditions, config, method, port):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    plan = {"dataset": "dataset26-v1", "method": method, "config": config,
            "conditions": conditions, "planned": len(conditions),
            "dataset_manifest_sha256": checksum(Path(root) / "datasets/dataset26-v1/manifest.json"),
            "implementation_hash": source_digest(Path(root)),
            "mode": "oracle_calibration" if method == "script" else "model_e2e"}
    plan_path = output / "plan.json"
    if plan_path.exists() and json.loads(plan_path.read_text()) != plan:
        raise ValueError("saved E2E plan uses different conditions, method, config, dataset or implementation")
    write_json(plan_path, plan)
    model, results = None, []
    for condition in conditions:
        target = output / "conditions" / (condition["id"] + ".json")
        if target.exists():
            saved = json.loads(target.read_text())
            if saved["result"]["termination_reason"] != "interrupted":
                results.append(saved)
                continue
        if model is None and method != "script":
            model = load_model(method, config)
        directory, result = run_case(
            Path(root), condition["case_id"], condition["build"], "target", method,
            copy.deepcopy(config), port=port, model=model, index=0, condition=condition,
            experiment="dataset26-e2e/" + output.name)
        saved = {"condition": condition, "run_directory": str(directory), "result": result}
        write_json(target, saved)
        results.append(saved)
        write_json(output / "summary.json", summarize(conditions, results, method))
        print(json.dumps({"condition": condition["id"], "oracle_verdict": result["oracle_verdict"],
                          "agent_verdict": result["agent_verdict"], "run_directory": str(directory)}), flush=True)
        if result["termination_reason"] == "interrupted":
            break
    summary = summarize(conditions, results, method)
    write_json(output / "summary.json", summary)
    return summary


def summarize(conditions, results, method):
    finished = [row for row in results if row["result"]["termination_reason"] != "interrupted"]
    valid = [row for row in finished if row["result"]["oracle_verdict"] in {"pass", "fail"}]
    expected_matches = sum(row["result"]["oracle_verdict"] == (
        "fail" if row["condition"]["build"] == "A" else "pass") for row in finished)
    index = {(row["condition"]["case_id"], row["condition"]["build"]): row["result"] for row in finished}
    paired_cases = {row["case_id"] for row in conditions
                    if {other["build"] for other in conditions if other["case_id"] == row["case_id"]} == {"A", "B"}}
    pairs = []
    for case_id in sorted(paired_cases):
        a, b = index.get((case_id, "A")), index.get((case_id, "B"))
        calibrated = bool(a and b and a["oracle_verdict"] == "fail" and b["oracle_verdict"] == "pass")
        pairs.append({"case_id": case_id, "complete": bool(a and b), "oracle_calibrated": calibrated,
                      "agent_pair_correct": bool(calibrated and a["agent_verdict"] == "fail"
                                                  and b["agent_verdict"] == "pass") if method != "script" else None})
    axes = {}
    for axis in sorted({row["evaluation_axis"] for row in conditions}):
        selected = [row for row in finished if row["condition"]["evaluation_axis"] == axis]
        selected_valid = [row for row in selected if row["result"]["oracle_verdict"] in {"pass", "fail"}]
        axes[axis] = {"planned": sum(row["evaluation_axis"] == axis for row in conditions),
                      "finished": len(selected), "oracle_valid": len(selected_valid),
                      "agent_oracle_agreements": sum(row["result"]["agent_verdict"] == row["result"]["oracle_verdict"]
                                                     for row in selected_valid) if method != "script" else None}
    return {"dataset": "dataset26-v1", "mode": "oracle_calibration" if method == "script" else "model_e2e",
            "planned": len(conditions), "finished": len(finished), "complete": len(finished) == len(conditions),
            "oracle_valid": len(valid), "oracle_expected_matches": expected_matches,
            "oracle_inconclusive": sum(row["result"]["oracle_verdict"] == "inconclusive" for row in finished),
            "infra_errors": sum(row["result"]["oracle_verdict"] == "infra_error" for row in finished),
            "agent_oracle_agreements": sum(row["result"]["agent_verdict"] == row["result"]["oracle_verdict"]
                                           for row in valid) if method != "script" else None,
            "model_evaluation_executed": method != "script" and any(row["result"]["model_calls"] > 0 for row in results),
            "pairs": pairs, "by_evaluation_axis": axes}


def exit_code(summary):
    if not summary["complete"] or summary["oracle_valid"] != summary["planned"]:
        return 4
    if summary["oracle_expected_matches"] != summary["planned"]:
        return 4
    if summary["mode"] == "model_e2e" and summary["agent_oracle_agreements"] != summary["planned"]:
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
