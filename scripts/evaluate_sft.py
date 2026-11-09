"""Resume per-app adapter evaluation against the frozen five-app baseline."""
import argparse
import csv
import hashlib
import json
import statistics
from pathlib import Path

import yaml

from guiqa.artifacts import source_digest, write_json
from guiqa.cli import load_config, load_model
from guiqa.model import PROMPT, REVIEW_PROMPT
from guiqa.runner import run_case


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "run", "report"])
    parser.add_argument("--config", type=Path, default=Path("configs/sft-eval.yaml"))
    parser.add_argument("--experiment", default="sft-lora-v1")
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--case")
    parser.add_argument("--port", type=int, default=13230)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    suite = yaml.safe_load((root / args.config).read_text())
    if args.case and args.case not in suite["cases"]:
        parser.error("--case must be one of the five frozen evaluation cases")
    if args.command == "run" and not args.case:
        parser.error("run requires --case; each worker runs all eight cells for one app")
    if args.adapter is None:
        args.adapter = Path(suite["adapter_path"])
    if args.command == "report":
        print(report(root, args.experiment, suite))
        return 0
    cases = [args.case] if args.case else suite["cases"]
    shards = [prepare(root, args, suite, case_id) for case_id in cases]
    if args.command == "plan":
        print(json.dumps([{"experiment": directory.name, "baseline_reused": len(baseline),
                           "adapter_planned": len(schedule["planned"]), "adapter_pending": len(pending)}
                          for directory, schedule, baseline, pending in shards], indent=2))
        return 0
    directory, schedule, baseline, pending = shards[0]
    directory.mkdir(parents=True, exist_ok=True)
    if not (directory / "schedule.json").exists():
        write_json(directory / "schedule.json", schedule)
        write_json(directory / "completed.json", baseline)
    completed = json.loads((directory / "completed.json").read_text())
    if pending:
        model = load_model(suite["method"], schedule["config"])
        for cell in pending:
            path, result = run_case(root, **cell, config=schedule["config"], model=model,
                                    port=args.port, experiment=directory.name)
            completed.append({**cell, "variant": "adapter", "run_id": result["run_id"],
                              "path": str(path.relative_to(root)),
                              "termination_reason": result["termination_reason"]})
            write_json(directory / "completed.json", completed)
            print(json.dumps(completed[-1]), flush=True)
            if result["termination_reason"] == "interrupted":
                break
    print(json.dumps({"shard": str(directory), "report_command":
                      f"python scripts/evaluate_sft.py report --experiment {args.experiment}"}))
    return 0


def prepare(root, args, suite, case_id):
    directory = root / "runs" / f"{args.experiment}-{case_id}"
    if (directory / "schedule.json").exists():
        schedule = json.loads((directory / "schedule.json").read_text())
        if args.adapter and str((root / args.adapter).resolve()) != schedule["config"]["adapter_path"]:
            raise ValueError("use a new experiment name for a different adapter")
        completed = json.loads((directory / "completed.json").read_text())
        baseline = [cell for cell in completed if cell["variant"] == "base"]
    else:
        config = load_config(root / suite["agent_config"])
        baseline = read_baseline(root, suite, case_id, config)
        adapter = str((root / args.adapter).resolve()) if args.adapter else None
        if args.command == "run" and not adapter:
            raise ValueError("a new evaluation requires --adapter PATH")
        planned = [{"case_id": case_id, "build": build, "workflow": workflow,
                    "method": suite["method"], "repeat": repeat}
                   for build in suite["builds"] for workflow in suite["workflows"]
                   for repeat in range(suite["repeats"])]
        schedule = {"experiment": directory.name, "suite": suite,
                    "config": {**config, "adapter_path": adapter}, "planned": planned,
                    "protocol": {"review_profile": "persistence",
                                 "operation_prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
                                 "review_prompt_sha256": hashlib.sha256(REVIEW_PROMPT.encode()).hexdigest(),
                                 "implementation_hash": source_digest(root)}}
        completed = baseline
    done = {cell_key(cell) for cell in completed if cell["variant"] == "adapter"
            and cell["termination_reason"] != "interrupted"}
    pending = [cell for cell in schedule["planned"] if cell_key(cell) not in done]
    return directory, schedule, baseline, pending


def read_baseline(root, suite, case_id, config):
    baseline = []
    for experiment in suite["baseline_experiments"]:
        directory = root / "runs" / experiment
        schedule = json.loads((directory / "schedule.json").read_text())
        if schedule["config"] != config:
            raise ValueError(f"{experiment}: baseline model or action/image budgets differ")
        for cell in json.loads((directory / "completed.json").read_text()):
            if cell["method"] != suite["method"] or cell["case_id"] != case_id:
                continue
            path = root / cell["path"]
            result = json.loads((path / "result.json").read_text())
            manifest = json.loads((path / "manifest.json").read_text())["config"]
            public = json.loads((root / "cases" / cell["case_id"] / "public.json").read_text())["workflows"][cell["workflow"]]
            if manifest["public"] != public or manifest["implementation_hash"] != suite["baseline_implementation_hash"]:
                raise ValueError(f"{result['run_id']}: frozen task or baseline source differs")
            baseline.append({**cell, "variant": "base", "source_experiment": experiment,
                             "termination_reason": result["termination_reason"]})
    expected = len(suite["builds"]) * len(suite["workflows"]) * suite["repeats"]
    if len({cell_key(cell) for cell in baseline}) != expected:
        raise ValueError(f"{case_id}: baseline does not cover all {expected} cells")
    return baseline


def cell_key(cell):
    return tuple(cell[key] for key in ("case_id", "build", "workflow", "repeat"))


def report(root, experiment, suite):
    cells = {}
    for case_id in suite["cases"]:
        path = root / "runs" / f"{experiment}-{case_id}" / "completed.json"
        if path.exists():
            for cell in json.loads(path.read_text()):
                cells[(cell["variant"], *cell_key(cell))] = cell
    results = {key: {**cell, **json.loads((root / cell["path"] / "result.json").read_text())}
               for key, cell in cells.items()}
    metrics = {}
    for variant in ("base", "adapter"):
        rows = [row for key, row in results.items() if key[0] == variant]
        targets = [row for row in rows if row["build"] == "A" and row["workflow"] == "target"]
        normal = [row for row in rows if row not in targets]
        matched = lambda row, verdict: row["agent_verdict"] == row["oracle_verdict"] == verdict
        target_pairs = sum(matched(row, "fail") and (variant, row["case_id"], "B", "target", row["repeat"]) in results
                           and matched(results[(variant, row["case_id"], "B", "target", row["repeat"])], "pass")
                           for row in targets)
        metrics[variant] = {"runs": len(rows), "detected": sum(matched(row, "fail") for row in targets),
                            "target_runs": len(targets), "false_positives": sum(row["agent_verdict"] == "fail" for row in normal),
                            "normal_runs": len(normal), "inconclusive": sum(row["oracle_verdict"] == "inconclusive" for row in rows),
                            "infra_error": sum(row["oracle_verdict"] == "infra_error" for row in rows),
                            "correct_cells": sum(matched(row, "fail" if row["build"] == "A" and row["workflow"] == "target" else "pass") for row in rows),
                            "correct_target_pairs": target_pairs,
                            "median_actions": statistics.median(row["actions"] for row in rows) if rows else None}
    pairs = []
    for case in suite["cases"]:
        for build in suite["builds"]:
            for workflow in suite["workflows"]:
                for repeat in range(suite["repeats"]):
                    row = {"case_id": case, "build": build, "workflow": workflow, "repeat": repeat,
                           "expected": "fail" if build == "A" and workflow == "target" else "pass"}
                    for variant in ("base", "adapter"):
                        result = results.get((variant, case, build, workflow, repeat), {})
                        row.update({f"{variant}_{key}": result.get(key, "not_started")
                                    for key in ("run_id", "agent_verdict", "oracle_verdict", "actions", "model_calls", "termination_reason")})
                    pairs.append(row)
    directory = root / "reports" / experiment
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "pairs.csv").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(pairs[0]))
        writer.writeheader()
        writer.writerows(pairs)
    write_json(directory / "comparison.json", {"experiment": experiment, "planned_per_variant": len(pairs),
                                               "method": suite["method"], "baseline_experiments": suite["baseline_experiments"],
                                               "metrics": metrics})
    lines = [f"# {experiment}", "", "| 权重 | 已运行 | 缺陷检出 | 正常误报 | 执行未完成 | 环境故障 | 四格正确 | target成对正确 | 中位动作 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for variant, item in metrics.items():
        lines.append(f"| {variant} | {item['runs']}/{len(pairs)} | {item['detected']}/{item['target_runs']} | "
                     f"{item['false_positives']}/{item['normal_runs']} | {item['inconclusive']} | {item['infra_error']} | "
                     f"{item['correct_cells']} | {item['correct_target_pairs']}/{item['target_runs']} | {item['median_actions']} |")
    lines += ["", "base引用历史完整结果；adapter采用相同qwen-controlled、任务、预算与persistence提示词。",
              "逐格配对及来源见 [pairs.csv](pairs.csv)。重复运行不增加应用样本数。"]
    (directory / "summary.md").write_text("\n".join(lines) + "\n")
    return directory


if __name__ == "__main__":
    raise SystemExit(main())
