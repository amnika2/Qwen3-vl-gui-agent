import csv
import json
import statistics
import uuid
from pathlib import Path

from .artifacts import write_json
from .runner import run_case


def evaluate(root, suite, methods, config, model=None, calibration=False, port=13200):
    experiment = ("calibration-" if calibration else "evaluation-") + uuid.uuid4().hex[:8]
    directory = root / "runs" / experiment
    directory.mkdir(parents=True)
    planned = [{"case_id": case, "build": build, "workflow": workflow, "method": method, "repeat": repeat}
               for case in suite["cases"] for build in suite.get("builds", ["A", "B"]) for workflow in ("target", "preservation")
               for method in methods for repeat in range(suite["repeats"])]
    write_json(directory / "schedule.json", {"experiment": experiment, "suite": suite,
               "planned": planned, "config": config})
    completed = []
    for cell in planned:
        run_dir, result = run_case(root, **cell, config=config, model=model if cell["method"] != "script" else None,
                                  experiment=experiment, port=port)
        expected = result.get("matrix_expected", "fail" if cell["build"] == "A" and cell["workflow"] == "target" else "pass")
        completed.append({**cell, "run_id": result["run_id"], "path": str(run_dir.relative_to(root)),
                          "expected": expected, "oracle_verdict": result["oracle_verdict"],
                          "matrix_matches": result["oracle_verdict"] == expected and result["agent_verdict"] == expected,
                          "agent_verdict": result["agent_verdict"]})
        write_json(directory / "completed.json", completed)
        print(json.dumps(completed[-1], ensure_ascii=False), flush=True)
        if result["termination_reason"] == "interrupted":
            break
    summary = summarize(root, directory)
    write_json(directory / "matrix.json", {"cells": completed,
        "passed": len(completed) == len(planned) and all(cell["matrix_matches"] for cell in completed)})
    return directory, summary


def summarize(root: Path, directory: Path):
    schedule = json.loads((directory / "schedule.json").read_text())
    completed_path = directory / "completed.json"
    completed = json.loads(completed_path.read_text()) if completed_path.exists() else []
    rows = []
    for cell in schedule["planned"]:
        found = next((item for item in completed if all(item[key] == cell[key] for key in cell)), None)
        result = json.loads((root / found["path"] / "result.json").read_text()) if found else {}
        rows.append({**cell, "run_id": result.get("run_id", ""), "started": bool(found),
                     "agent_verdict": result.get("agent_verdict", "not_started"),
                     "oracle_verdict": result.get("oracle_verdict", "not_started"),
                     "elapsed_seconds": result.get("elapsed_seconds", ""), "actions": result.get("actions", ""),
                     "model_calls": result.get("model_calls", ""), "termination_reason": result.get("termination_reason", "not_started")})
    report_dir = root / "reports" / schedule["experiment"]
    report_dir.mkdir(parents=True, exist_ok=True)
    with (report_dir / "results.csv").open("w") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]) if rows else ["run_id"])
        writer.writeheader()
        writer.writerows(rows)
    lines = [f"# {schedule['experiment']}", "", f"计划 {len(rows)} 次；已启动 {sum(row['started'] for row in rows)} 次。", "",
             "| 方法 | 缺陷检出 | 正常误报 | 执行未完成 | 环境故障 | 成对正确 | 中位耗时(秒) |",
             "|---|---|---|---|---|---|---|"]
    for method in sorted({row["method"] for row in rows}):
        group = [row for row in rows if row["method"] == method]
        targets = [row for row in group if row["build"] == "A" and row["workflow"] == "target"]
        normal = [row for row in group if row not in targets]
        detected = sum(row["agent_verdict"] == "fail" and row["oracle_verdict"] == "fail" for row in targets)
        false_positive = sum(row["agent_verdict"] == "fail" for row in normal)
        incomplete = sum(row["oracle_verdict"] == "inconclusive" for row in group)
        infra = sum(row["oracle_verdict"] == "infra_error" for row in group)
        pairs = 0
        for row in targets:
            match = next((other for other in group if other["case_id"] == row["case_id"] and other["workflow"] == "target"
                          and other["build"] == "B" and other["repeat"] == row["repeat"]), None)
            pairs += bool(row["agent_verdict"] == "fail" and row["oracle_verdict"] == "fail" and match
                          and match["agent_verdict"] == "pass" and match["oracle_verdict"] == "pass")
        durations = [row["elapsed_seconds"] for row in group if row["started"]]
        median = f"{statistics.median(durations):.2f}" if durations else "—"
        lines.append(f"| {method} | {detected}/{len(targets)} | {false_positive}/{len(normal)} | {incomplete}/{len(group)} | {infra}/{len(group)} | {pairs}/{len(targets)} | {median} |")
    lines += ["", "逐次结果、终止原因、动作数及模型调用见 [results.csv](results.csv)。",
              "固定脚本使用预编写定位；Qwen 使用自然语言和截图。重复运行不增加应用样本数。", "",
              f"数据分组：{schedule.get('suite', {}).get('split', 'unspecified')}。正式分析批次由 configs/analysis.yaml 指定。"]
    (report_dir / "summary.md").write_text("\n".join(lines) + "\n")
    return report_dir
