import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path

import yaml


def main():
    root = Path(__file__).resolve().parents[1]
    analysis = yaml.safe_load((root / "configs/analysis.yaml").read_text())
    rows = []
    primary_ids = set()
    for experiment in analysis["experiments"] + analysis.get("aborted_experiments", []):
        directory = root / "runs" / experiment
        schedule = json.loads((directory / "schedule.json").read_text())
        completed_path = directory / "completed.json"
        completed = json.loads(completed_path.read_text()) if completed_path.exists() else []
        for cell in schedule["planned"]:
            found = next((item for item in completed if all(item[key] == cell[key] for key in cell)), None)
            result = json.loads((root / found["path"] / "result.json").read_text()) if found else {}
            manifest = json.loads((root / found["path"] / "manifest.json").read_text()) if found else {}
            model_log = root / found["path"] / "model.jsonl" if found else None
            calls = [json.loads(line) for line in model_log.read_text().splitlines()] if model_log and model_log.exists() else []
            primary_ids.add(result.get("run_id"))
            rows.append({"split": schedule["suite"]["split"], "experiment": experiment, **cell,
                         "run_id": result.get("run_id", ""), "started": bool(found),
                         "agent_verdict": result.get("agent_verdict", "not_started"),
                         "oracle_verdict": result.get("oracle_verdict", "not_started"),
                         "termination_reason": result.get("termination_reason", "not_started"),
                         "actual": result.get("actual", ""), "elapsed_seconds": result.get("elapsed_seconds", ""),
                         "actions": result.get("actions", ""), "model_calls": result.get("model_calls", ""),
                         "gpu_peak_mib": result.get("gpu", {}).get("peak_allocated_mib", ""),
                         "model_load_seconds": result.get("gpu", {}).get("load_seconds", ""),
                         "inference_seconds": sum(call.get("elapsed_seconds", 0) for call in calls),
                         "oracle_reason": result.get("oracle_reason", ""), "agent_observation": result.get("observation", ""),
                         "evidence_steps": result.get("evidence_steps", []),
                         "implementation_hash": manifest.get("config", {}).get("implementation_hash", ""),
                         "config_hash": result.get("config_hash", ""), "attempt_id": result.get("attempt_id", "")})
    aborted = [row for row in rows if row["experiment"] in analysis.get("aborted_experiments", [])]
    rows = [row for row in rows if row["experiment"] in analysis["experiments"]]
    write_csv(root / "reports/results.csv", rows)
    conditions = defaultdict(list)
    for row in rows:
        conditions[(row["split"], row["case_id"], row["build"], row["workflow"], row["method"])].append(row)
    write_csv(root / "reports/by-condition.csv", [
        {**dict(zip(("split", "case_id", "build", "workflow", "method"), key)), "planned": len(group),
         "started": sum(row["started"] for row in group),
         **{f"agent_{verdict}": sum(row["agent_verdict"] == verdict for row in group) for verdict in ("pass", "fail", "blocked")},
         **{f"oracle_{verdict}": sum(row["oracle_verdict"] == verdict for row in group) for verdict in ("pass", "fail", "inconclusive", "infra_error")}}
        for key, group in sorted(conditions.items())])
    write_csv(root / "reports/aborted-runs.csv", aborted)
    errors = [row for row in rows if row["started"] and
              (row["oracle_verdict"] in ("inconclusive", "infra_error") or row["agent_verdict"] != row["oracle_verdict"])]
    write_csv(root / "reports/failures.csv", errors, list(rows[0]))
    summaries = summarize(rows, len(analysis["calibrations"]))
    if aborted:
        summaries += (f"\n## 保留的历史轮次\n\n此前计划 {len(aborted)} 次，完成/中断落盘 {sum(row['started'] for row in aborted)} 次，"
                      f"未完成排程 {sum(not row['started'] for row in aborted)} 次。每个计划格子均保留，"
                      "见 [中断轮次全部排程](aborted-runs.csv)。\n\n| 实验 | 原因 |\n|---|---|\n")
        for experiment in analysis.get("aborted_experiments", []):
            summaries += f"| {experiment} | {analysis.get('aborted_reasons', {}).get(experiment, '')} |\n"
    (root / "reports/summary.md").write_text(summaries)
    trials = []
    for path in sorted((root / "runs").glob("*/result.json")):
        result = json.loads(path.read_text())
        if result.get("run_id") in primary_ids:
            continue
        trials.append({key: result.get(key, "") for key in (
            "run_id", "case_id", "workflow", "method", "experiment", "agent_verdict", "oracle_verdict", "termination_reason")})
    write_csv(root / "reports/development-and-calibration-trials.csv", trials)
    demo(root)
    print(f"Completed {sum(row['started'] for row in rows)}/{len(rows)} planned runs; reports/summary.md")


def write_csv(path, rows, fields=None):
    names = fields or (list(rows[0]) if rows else ["run_id"])
    with path.open("w") as file:
        writer = csv.DictWriter(file, fieldnames=names)
        writer.writeheader()
        writer.writerows(rows)


def summarize(rows, calibrated):
    started = sum(row["started"] for row in rows)
    applications = {split: len({row["case_id"] for row in rows if row["split"] == split}) for split in ("development", "holdout")}
    lines = ["# GUIQA 对照结果", "", f"计划 {len(rows)} 次，已完成 {started} 次，未完成排程 {len(rows) - started} 次。",
             "", "模型：冻结 Qwen3-VL-8B-Instruct，BF16，server402 两张 RTX 4090。",
             f"30 次浏览器动作、600 秒执行预算，每条件 2 次；开发 {applications['development']} 个应用，留出 {applications['holdout']} 个应用。",
             "固定脚本使用预先编写定位，两个 Qwen 方法使用相同业务用例及截图；改进版增加动作进度文本。", "",
             "| 数据 | 方法 | 缺陷检出 | 正常误报 | 执行未完成 | 环境故障 | 成对正确 | 中位秒 | 中位动作 | 中位模型调用 | 峰值显存 MiB |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    if started == len(rows):
        findings = []
        for method in ("qwen-base", "qwen-improved"):
            group = [row for row in rows if row["method"] == method]
            target = [row for row in group if row["build"] == "A" and row["workflow"] == "target"]
            normal = [row for row in group if row not in target]
            detected = sum(row["agent_verdict"] == "fail" and row["oracle_verdict"] == "fail" for row in target)
            alarms = sum(row["agent_verdict"] == "fail" for row in normal)
            findings.append(f"{method}：有效检出 {detected}/{len(target)}，正常误报 {alarms}/{len(normal)}")
        lines[4:4] = ["；".join(findings) + "。", ""]
    groups = defaultdict(list)
    for row in rows:
        groups[(row["split"], row["method"])].append(row)
    for (split, method), group in sorted(groups.items()):
        target = [row for row in group if row["build"] == "A" and row["workflow"] == "target"]
        normal = [row for row in group if row not in target]
        detected = sum(row["agent_verdict"] == "fail" and row["oracle_verdict"] == "fail" for row in target)
        false_positive = sum(row["agent_verdict"] == "fail" for row in normal)
        incomplete = sum(row["oracle_verdict"] == "inconclusive" for row in group)
        infra = sum(row["oracle_verdict"] == "infra_error" for row in group)
        pairs = 0
        for row in target:
            match = next((other for other in group if other["case_id"] == row["case_id"] and other["workflow"] == "target"
                          and other["build"] == "B" and other["repeat"] == row["repeat"]), None)
            pairs += bool(row["agent_verdict"] == "fail" and row["oracle_verdict"] == "fail" and match
                          and match["agent_verdict"] == "pass" and match["oracle_verdict"] == "pass")
        values = lambda key: [row[key] for row in group if row[key] != ""]
        median = lambda key: f"{statistics.median(values(key)):.2f}" if values(key) else "—"
        peak = max(values("gpu_peak_mib"), default=0)
        lines.append(f"| {split} | {method} | {detected}/{len(target)} | {false_positive}/{len(normal)} | {incomplete}/{len(group)} | {infra}/{len(group)} | {pairs}/{len(target)} | {median('elapsed_seconds')} | {median('actions')} | {median('model_calls')} | {peak:.0f} |")
    lines += ["", "[逐次数据](results.csv) · [执行异常与判定不一致](failures.csv) · [历史校准和开发试点](development-and-calibration-trials.csv) · [演示](demo.md)", "",
              "主分析取选定判据版本下的首次排程，模型失败和单次环境故障不从分母删除。未启动排程也保留在 CSV 和分母中。",
              "判据修正前的整批排程按原因单列保留，不挑选单次成功记录替换。",
              "执行未完成指独立判据未取得有效终局证据；Agent 的 blocked 和已有证据却未判定另表列出。",
              "两次重复检查环境波动，不增加应用样本数。", "",
              "## 按终止状态的耗时", "", "| 数据 | 方法 | 终止原因 | 次数 | 中位秒 |", "|---|---|---|---|---|"]
    term_groups = defaultdict(list)
    for row in rows:
        if row["started"]:
            term_groups[(row["split"], row["method"], row["termination_reason"])].append(row["elapsed_seconds"])
    for keys, values in sorted(term_groups.items()):
        lines.append("| " + " | ".join(keys) + f" | {len(values)} | {statistics.median(values):.2f} |")
    lines += ["", "## Agent 判定分布", "", "| 数据 | 方法 | pass | fail | blocked | blocked 但已有独立证据 | 已判定但证据不足 |", "|---|---|---|---|---|---|---|"]
    for (split, method), group in sorted(groups.items()):
        counts = {value: sum(row["agent_verdict"] == value for row in group) for value in ("pass", "fail", "blocked")}
        valid_blocked = sum(row["agent_verdict"] == "blocked" and row["oracle_verdict"] in ("pass", "fail") for row in group)
        unsupported = sum(row["agent_verdict"] in ("pass", "fail") and row["oracle_verdict"] == "inconclusive" for row in group)
        lines.append(f"| {split} | {method} | {counts['pass']} | {counts['fail']} | {counts['blocked']} | {valid_blocked} | {unsupported} |")
    lines += ["", "## 逐应用结果", "", "| 应用 | 方法 | 缺陷检出 | 正常误报 | 证据不足 | 环境故障 | Agent blocked |",
              "|---|---|---|---|---|---|---|"]
    app_groups = defaultdict(list)
    for row in rows:
        app_groups[(row["case_id"], row["method"])].append(row)
    for (case, method), group in sorted(app_groups.items()):
        target = [row for row in group if row["build"] == "A" and row["workflow"] == "target"]
        normal = [row for row in group if row not in target]
        detected = sum(row["agent_verdict"] == "fail" and row["oracle_verdict"] == "fail" for row in target)
        alarms = sum(row["agent_verdict"] == "fail" for row in normal)
        incomplete = sum(row["oracle_verdict"] == "inconclusive" for row in group)
        infra = sum(row["oracle_verdict"] == "infra_error" for row in group)
        blocked = sum(row["agent_verdict"] == "blocked" for row in group)
        lines.append(f"| {case} | {method} | {detected}/{len(target)} | {alarms}/{len(normal)} | {incomplete}/{len(group)} | {infra}/{len(group)} | {blocked}/{len(group)} |")
    lines += ["", "按应用、版本、workflow 和方法分组的完整计数见 [by-condition.csv](by-condition.csv)。",
              "逐次 CSV 记录模型调用耗时之和；首次模型加载耗时另列，后续运行复用已加载权重。"]
    lines += ["", "## 校准与资源", "",
              f"{calibrated} 个应用的独立参考脚本四格校准均为 8/8 符合预期，详见 [M0 报告](milestone-M0.md)。",
              "前三个应用的源码和预算冻结见 `docs/protocol-freeze.json`；原候选 Ampache 和 Anchor CMS 经脚本校准后加入留出，",
              "推理、动作和进度控制器逐字节一致性检查见 `docs/protocol-extension-freeze.json`，对应源码快照均保留。"]
    return "\n".join(lines) + "\n"


def demo(root):
    matrix = json.loads((root / "runs/calibration-928458dc/matrix.json").read_text())["cells"]
    selected = [next(row for row in matrix if row["build"] == build and row["workflow"] == "target" and row["repeat"] == 0) for build in ("A", "B")]
    lines = ["# 演示索引", "", "## Akaunting：同一参考脚本的缺陷版和修复版", "",
             "| 缺陷版 | 修复版 |", "|---|---|",
             f"| ![截断金额](../{selected[0]['path']}/screenshots/004.png) | ![完整金额](../{selected[1]['path']}/screenshots/004.png) |",
             "| $1,234.57 | $1,234,567.89 |", "",
             "## emlog：开发轨迹中的输入机制", "",
             "| 原始 Qwen：追加导致错误输入 | 进度记录：先全选，输入正确 |", "|---|---|",
             "| ![追加输入](../runs/20261106T163802Z-a2b71478/screenshots/002.png) | ![完整替换](../runs/20261106T164551Z-0516b110/screenshots/003.png) |",
             "", "这组开发试点的两个方法最终都未重开验证，均为 inconclusive。截图展示输入阶段的机制变化；最终有效完成率以正式对照表为准。", "",
             "## Qwen 完整通过正常业务用例", "",
             "Anchor CMS 原始 Qwen 留出轨迹：创建 hoursnote=Desk hours，保存后检查列表，Agent 与独立判据均为 pass。",
             "11 次浏览器动作、12 次模型调用，14.66 秒。该记录属于正常功能通过，不是缺陷检出。", "",
             "![模型验证保存后的变量](../runs/20261107T042614Z-6a7449fc/screenshots/011.png)", "",
             "[完整报告](../runs/20261107T042614Z-6a7449fc/report.md)", "",
             "Qwen 的逐步动作、原始输出和独立判据分别在每个 run 的 trace.jsonl、model.jsonl、private/oracle.json 中。"]
    (root / "reports/demo.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
