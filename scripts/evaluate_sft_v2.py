"""Run fresh base/selected-adapter evaluation in independent eight-cell workers."""
import argparse
import csv
import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path

import yaml

from evaluate_sft import cell_key
from guiqa.artifacts import digest, write_json
from guiqa.cli import load_config, load_model
from guiqa.model import PROMPT, REVIEW_PROMPT
from guiqa.review_evidence import review_prompt
from guiqa.runner import run_case

INFERENCE_FILES = [f"src/guiqa/{name}.py" for name in (
    "runner", "model", "cli", "actions", "browser", "control", "review_evidence", "ledger", "oracle",
    "native", "extra_instances", "extra_cases")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "run", "report"])
    parser.add_argument("--config", type=Path, default=Path("configs/sft-eval-v2.yaml"))
    parser.add_argument("--experiment", default="sft-lora-v2")
    parser.add_argument("--variant", choices=["base", "adapter"])
    parser.add_argument("--case")
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--port", type=int, default=13240)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    suite = yaml.safe_load((root / args.config).read_text())
    if args.case and args.case not in suite["cases"]:
        parser.error("--case must be one of the five frozen evaluation cases")
    if args.command == "run" and (not args.case or not args.variant):
        parser.error("run requires --case and --variant")
    if args.command == "report":
        print(report(root, args.experiment, suite))
        return 0
    args.adapter = args.adapter or Path(suite["adapter_path"])
    core = inference_core(root, suite)
    variants = [args.variant] if args.variant else ["base", "adapter"]
    cases = [args.case] if args.case else suite["cases"]
    workers = [prepare(root, args, suite, variant, case_id, core)
               for variant in variants for case_id in cases]
    if args.command == "plan":
        print(json.dumps([{"worker": directory.name, "planned": len(schedule["planned"]),
                           "pending": len(pending), "inference_core_sha256": core["sha256"]}
                          for directory, schedule, pending in workers], indent=2))
        return 0
    directory, schedule, pending = workers[0]
    directory.mkdir(parents=True, exist_ok=True)
    if not (directory / "schedule.json").exists():
        write_json(directory / "schedule.json", schedule)
        write_json(directory / "completed.json", [])
    completed = json.loads((directory / "completed.json").read_text())
    if pending:
        model = load_model(suite["method"], schedule["config"])
        for cell in pending:
            path, result = run_case(root, **cell, config=schedule["config"], model=model,
                                    port=args.port, experiment=directory.name)
            row = {**cell, "variant": args.variant, "run_id": result["run_id"],
                   "path": str(path.relative_to(root)),
                   "inference_core_sha256": core["sha256"],
                   "termination_reason": result["termination_reason"]}
            completed.append(row)
            write_json(directory / "completed.json", completed)
            print(json.dumps(row), flush=True)
            if result["termination_reason"] == "interrupted":
                break
    print(json.dumps({"worker": str(directory), "report_command":
                      f"python scripts/evaluate_sft_v2.py report --experiment {args.experiment}"}))
    return 0


def inference_core(root, suite):
    paths = INFERENCE_FILES + [suite["agent_config"]] + [f"cases/{case}/{name}.json" for case in suite["cases"]
                              for name in ("public", "fixtures.private", "private")]
    files = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in paths}
    config = load_config(root / suite["agent_config"])
    active_review_prompt = review_prompt() if config.get("review_format") == "evidence_v1" else REVIEW_PROMPT
    return {"sha256": digest(files), "files": files,
            "operation_prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
            "review_prompt_sha256": hashlib.sha256(active_review_prompt.encode()).hexdigest()}


def prepare(root, args, suite, variant, case_id, core):
    directory = root / "runs" / f"{args.experiment}-{variant}-{case_id}"
    if (directory / "schedule.json").exists():
        schedule = json.loads((directory / "schedule.json").read_text())
        if schedule["inference_core"] != core:
            raise ValueError("inference core changed; use a new experiment name")
        if variant == "adapter" and schedule["config"]["adapter_path"] != str((root / args.adapter).resolve()):
            raise ValueError("use a new experiment name for a different adapter")
        completed = json.loads((directory / "completed.json").read_text())
    else:
        config = load_config(root / suite["agent_config"])
        config["inference_core_sha256"] = core["sha256"]
        config.pop("adapter_path", None)
        if variant == "adapter":
            config["adapter_path"] = str((root / args.adapter).resolve())
            if args.command == "run":
                config["adapter_sha256"] = hashlib.sha256(
                    (Path(config["adapter_path"]) / "adapter_model.safetensors").read_bytes()).hexdigest()
        planned = [{"case_id": case_id, "build": build, "workflow": workflow,
                    "method": suite["method"], "repeat": repeat}
                   for build in suite["builds"] for workflow in suite["workflows"]
                   for repeat in range(suite["repeats"])]
        schedule = {"experiment": directory.name, "variant": variant, "suite": suite,
                    "config": config, "planned": planned, "inference_core": core}
        completed = []
    done = {cell_key(cell) for cell in completed if cell["termination_reason"] != "interrupted"}
    pending = [cell for cell in schedule["planned"] if cell_key(cell) not in done]
    return directory, schedule, pending


def matches(row, verdict):
    return row["agent_verdict"] == row["oracle_verdict"] == verdict


def variant_metrics(results, variant, suite):
    rows = [row for key, row in results.items() if key[0] == variant]
    targets = [row for row in rows if row["build"] == "A" and row["workflow"] == "target"]
    normal = [row for row in rows if row not in targets]
    paired = sum(matches(row, "fail") and (variant, row["case_id"], "B", "target", row["repeat"]) in results
                 and matches(results[(variant, row["case_id"], "B", "target", row["repeat"])], "pass")
                 for row in targets)
    four_cells = 0
    for case in suite["cases"]:
        for repeat in range(suite["repeats"]):
            group = [results.get((variant, case, build, workflow, repeat))
                     for build in suite["builds"] for workflow in suite["workflows"]]
            four_cells += all(row and matches(row, "fail" if row["build"] == "A" and row["workflow"] == "target" else "pass")
                              for row in group)
    active_rows = [row for row in rows if row.get("control_trigger") == "model_proposed_finish"]
    active_finish = len(active_rows)
    return {"runs": len(rows),
            "completed_runs": sum(row["termination_reason"] != "interrupted" for row in rows),
            "detected": sum(matches(row, "fail") for row in targets), "target_runs": len(targets),
            "false_positives": sum(row["agent_verdict"] == "fail" for row in normal), "normal_runs": len(normal),
            "correct_cells": sum(matches(row, "fail" if row["build"] == "A" and row["workflow"] == "target" else "pass") for row in rows),
            "correct_four_cell_sets": four_cells, "correct_target_pairs": paired,
            "active_finish": active_finish, "active_finish_rate": active_finish / len(rows) if rows else None,
            "active_finish_correct": sum(matches(row, "fail" if row["build"] == "A" and row["workflow"] == "target" else "pass")
                                         for row in active_rows),
            "active_finish_blocked_or_inconclusive": sum(row["agent_verdict"] == "blocked" or row["oracle_verdict"] == "inconclusive"
                                                         for row in active_rows),
            "active_finish_infra_error": sum(row["oracle_verdict"] == "infra_error" for row in active_rows),
            "guard_reasons": dict(Counter(row["review_guard"] for row in rows if row.get("review_guard"))),
            "agent_blocked": sum(row["agent_verdict"] == "blocked" for row in rows),
            "inconclusive": sum(row["oracle_verdict"] == "inconclusive" for row in rows),
            "infra_error": sum(row["oracle_verdict"] == "infra_error" for row in rows),
            "median_actions": statistics.median(row["actions"] for row in rows) if rows else None}


def report(root, experiment, suite):
    cells, protocols = {}, {}
    for variant in ("base", "adapter"):
        for case in suite["cases"]:
            directory = root / "runs" / f"{experiment}-{variant}-{case}"
            if not directory.exists():
                continue
            schedule = json.loads((directory / "schedule.json").read_text())
            protocols[directory.name] = schedule["inference_core"]
            for cell in json.loads((directory / "completed.json").read_text()):
                cells[(variant, *cell_key(cell))] = cell
    results = {key: {**cell, **json.loads((root / cell["path"] / "result.json").read_text())}
               for key, cell in cells.items()}
    metrics = {variant: variant_metrics(results, variant, suite) for variant in ("base", "adapter")}
    protocol_pairs = {}
    for case in suite["cases"]:
        base = protocols.get(f"{experiment}-base-{case}")
        adapter = protocols.get(f"{experiment}-adapter-{case}")
        protocol_pairs[case] = (base == adapter) if base and adapter else None
    pairs = []
    for case in suite["cases"]:
        for build in suite["builds"]:
            for workflow in suite["workflows"]:
                for repeat in range(suite["repeats"]):
                    row = {"case_id": case, "build": build, "workflow": workflow, "repeat": repeat,
                           "expected": "fail" if build == "A" and workflow == "target" else "pass",
                           "inference_core_matches": protocol_pairs[case]}
                    for variant in ("base", "adapter"):
                        result = results.get((variant, case, build, workflow, repeat), {})
                        row.update({f"{variant}_{key}": result.get(key, "" if result else "not_started")
                                    for key in ("run_id", "agent_verdict", "oracle_verdict", "actions", "model_calls",
                                                "termination_reason", "control_trigger", "review_guard", "review_images")})
                    pairs.append(row)
    directory = root / "reports" / experiment
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "pairs.csv").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(pairs[0]))
        writer.writeheader()
        writer.writerows(pairs)
    write_json(directory / "comparison.json", {"experiment": experiment, "planned_per_variant": len(pairs),
                                               "method": suite["method"], "baseline_reused": False,
                                               "metrics": metrics, "inference_core_pairs": protocol_pairs,
                                               "worker_inference_core": protocols})
    lines = [f"# {experiment}", "", "当前截图环境下重新执行 base 和 selected adapter，每种权重 40 格。", "",
             "| 权重 | 完成 | 正确格 | 缺陷检出 | 正常误报 | 四格全对 | target成对正确 | 主动finish | 中位动作 | 未完成 | 环境故障 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    sets = len(suite["cases"]) * suite["repeats"]
    for variant, item in metrics.items():
        lines.append(f"| {variant} | {item['completed_runs']}/{len(pairs)} | {item['correct_cells']}/{len(pairs)} | "
                     f"{item['detected']}/{item['target_runs']} | {item['false_positives']}/{item['normal_runs']} | "
                     f"{item['correct_four_cell_sets']}/{sets} | {item['correct_target_pairs']}/{sets} | "
                     f"{item['active_finish']}/{item['runs']} | {item['median_actions']} | {item['inconclusive']} | {item['infra_error']} |")
    lines += ["", "主动finish计数来自model_proposed_finish，分母含全部已运行格。四格全对按应用和重复编号计数。",
              "失败全部保留；interrupted的后续同格记录覆盖报告视图，原始行保留。", "",
              f"逐应用推理核心一致性：{json.dumps(protocol_pairs, ensure_ascii=False)}。",
              "推理文件与五应用任务/fixture的逐文件hash见comparison.json；训练和builder源码不参与推理hash。", ""]
    for variant, item in metrics.items():
        lines.append(f"{variant} guard原因：{json.dumps(item['guard_reasons'], ensure_ascii=False)}。")
        lines.append(f"{variant} 主动finish细分：agent/oracle按预期共同正确 {item['active_finish_correct']}；"
                     f"blocked或oracle inconclusive {item['active_finish_blocked_or_inconclusive']}；"
                     f"主动finish后的环境故障 {item['active_finish_infra_error']}。")
    lines += ["", "逐格结果和证据来源见 [pairs.csv](pairs.csv)。重复不增加应用数。"]
    (directory / "summary.md").write_text("\n".join(lines) + "\n")
    return directory


if __name__ == "__main__":
    raise SystemExit(main())
