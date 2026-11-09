"""Collect distinct supplementary scenarios, retaining every failed attempt."""
import argparse
import json
import shutil
import tarfile
import uuid
from pathlib import Path

from guiqa.artifacts import source_digest, write_json
from guiqa.cli import load_config
from guiqa.runner import run_case
from guiqa.sft_scenarios import load_scenarios


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("configs/sft-supplementary-scenarios.yaml"))
    parser.add_argument("--method", choices=["script", "sft"], default="sft")
    parser.add_argument("--scenario", action="append")
    parser.add_argument("--resume")
    parser.add_argument("--port", type=int, default=13219)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    directory, scenarios, config, method = prepare_batch(root, args)
    snapshot_source(root, directory)
    completed_path = directory / "completed.json"
    completed = json.loads(completed_path.read_text())
    successful = {cell["scenario_id"] for cell in completed if cell["matrix_matches"]}
    for scenario in scenarios:
        if scenario["id"] in successful:
            continue
        path, result = run_case(root, scenario["case_id"], scenario["build"], scenario["workflow"],
            method, config, port=args.port, experiment=directory.name, scenario=scenario)
        cell = {"scenario_id": scenario["id"], "case_id": scenario["case_id"], "build": scenario["build"],
                "workflow": scenario["workflow"], "data_split": scenario["data_split"], "method": method,
                "run_id": result["run_id"], "path": str(path.relative_to(root)), "expected": "pass",
                "oracle_verdict": result["oracle_verdict"], "agent_verdict": result["agent_verdict"],
                "matrix_matches": result["oracle_verdict"] == result["agent_verdict"] == "pass"}
        completed.append(cell)
        write_json(completed_path, completed)
        print(json.dumps(cell, ensure_ascii=False), flush=True)
        if result["termination_reason"] == "interrupted":
            break
    successful = {cell["scenario_id"] for cell in completed if cell["matrix_matches"]}
    matrix = {"planned": len(scenarios), "attempts": len(completed), "successful_scenarios": len(successful),
              "passed": len(successful) == len(scenarios),
              "pending_scenarios": [scenario["id"] for scenario in scenarios if scenario["id"] not in successful]}
    write_json(directory / "matrix.json", matrix)
    print(json.dumps({"experiment": directory.name, **matrix}, ensure_ascii=False, indent=2))
    return 0 if matrix["passed"] else 4


def prepare_batch(root, args):
    if args.resume:
        directory = root / "runs" / args.resume
        schedule = json.loads((directory / "schedule.json").read_text())
        all_scenarios = load_scenarios(directory / "catalog.yaml")
        selected = set(schedule["scenario_ids"])
        return directory, [scenario for scenario in all_scenarios if scenario["id"] in selected], schedule["config"], schedule["method"]
    scenarios = load_scenarios(root / args.catalog)
    if args.scenario:
        selected = set(args.scenario)
        unknown = selected - {scenario["id"] for scenario in scenarios}
        if unknown:
            raise ValueError(f"unknown scenarios: {sorted(unknown)}")
        scenarios = [scenario for scenario in scenarios if scenario["id"] in selected]
    directory = root / "runs" / ("supplementary-" + uuid.uuid4().hex[:8])
    directory.mkdir()
    shutil.copy2(root / args.catalog, directory / "catalog.yaml")
    config = load_config(root / "configs/agent.yaml")
    write_json(directory / "schedule.json", {"experiment": directory.name, "method": args.method,
        "scenario_ids": [scenario["id"] for scenario in scenarios], "config": config,
        "planned": [{key: scenario[key] for key in ("id", "case_id", "build", "workflow", "family", "data_split", "fingerprint")}
                    for scenario in scenarios]})
    write_json(directory / "completed.json", [])
    return directory, scenarios, config, args.method


def snapshot_source(root, directory):
    destination = directory / "source-snapshots" / (source_digest(root) + ".tar.gz")
    if destination.exists():
        return
    destination.parent.mkdir(exist_ok=True)
    files = list((root / "src/guiqa").rglob("*.py")) + [root / "scripts/collect_supplementary.py"]
    files += [root / "cases" / case / name for case in ("sweeper-034", "sweeper-053")
              for name in ("public.json", "fixtures.private.json", "private.json")]
    with tarfile.open(destination, "w:gz") as archive:
        for path in files:
            archive.add(path, arcname=str(path.relative_to(root)))


if __name__ == "__main__":
    raise SystemExit(main())
