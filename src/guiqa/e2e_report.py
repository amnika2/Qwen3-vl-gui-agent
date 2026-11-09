"""Paired summaries of frozen end-to-end experiment cells."""

import csv
import json
from pathlib import Path
from statistics import mean

from .artifacts import write_json


def summarize(root, experiment_dir):
    directory = Path(root) / experiment_dir
    protocol = json.loads((directory / "protocol.json").read_text())
    rows = read_rows(directory, protocol)
    groups = {group["id"]: group_metrics([row for row in rows if row["group"] == group["id"]])
              for group in protocol["groups"]}
    model_pairs = [("sft-full", "base-full"), ("grpo-full", "sft-full"), ("grpo-full", "base-full")]
    ablation_pairs = [("grpo-full", "grpo-no-" + mechanism) for mechanism in ("planning", "memory", "reflection")]
    comparisons, paired_rows = [], []
    for kind, pairs in (("model", model_pairs), ("ablation", ablation_pairs)):
        for group, reference in pairs:
            if group not in groups or reference not in groups:
                continue
            comparison, differences = paired_comparison(rows, groups, group, reference)
            comparisons.append({"kind": kind, **comparison})
            paired_rows.extend({"kind": kind, **row} for row in differences)
    summary = {"experiment": protocol.get("id", directory.name), "planned": len(rows),
               "started": sum(row["started"] for row in rows),
               "not_started": sum(not row["started"] for row in rows),
               "groups": groups,
               "model_comparisons": [item for item in comparisons if item["kind"] == "model"],
               "ablations": [item for item in comparisons if item["kind"] == "ablation"]}
    write_csv(directory / "results.csv", rows)
    write_csv(directory / "paired-differences.csv", paired_rows)
    write_json(directory / "summary.json", summary)
    (directory / "summary.md").write_text(render_summary(summary, paired_rows))
    return summary


def read_rows(directory, protocol):
    configurations = {group["id"]: group["config"] for group in protocol["groups"]}
    rows = []
    for cell in protocol["cells"]:
        path = directory / "cells" / (cell["id"] + ".json")
        saved = json.loads(path.read_text()) if path.exists() else {}
        result = saved.get("result", {})
        started = bool(saved)
        expected = result.get("matrix_expected", "fail" if cell["build"] == "A" and cell["workflow"] == "target" else "pass")
        agent, oracle = result.get("agent_verdict", "not_started"), result.get("oracle_verdict", "not_started")
        usage = result.get("gpu", {})
        runtime = configurations[cell["group"]].get("agent_runtime", {})
        enabled = runtime.get("enabled", False)
        rows.append({"id": cell["id"], "group": cell["group"], "case_id": cell["case_id"],
                     "build": cell["build"], "workflow": cell["workflow"], "repeat": cell.get("repeat", 0),
                     "run_id": result.get("run_id", ""), "run_dir": saved.get("run_dir", ""),
                     "started": started, "matrix_expected": expected,
                     "agent_verdict": agent, "oracle_verdict": oracle,
                     "execution_completed": oracle in {"pass", "fail"},
                     "e2e_success": agent == oracle == expected,
                     "actions": result.get("actions", 0), "model_calls": result.get("model_calls", 0),
                     "prompt_tokens": usage.get("prompt_tokens", 0), "completion_tokens": usage.get("completion_tokens", 0),
                     "generation_seconds": usage.get("generation_seconds", 0),
                     "usage_recorded": started and all(key in usage for key in ("prompt_tokens", "completion_tokens", "generation_seconds")),
                     "elapsed_seconds": result.get("elapsed_seconds", 0),
                     "termination_reason": result.get("termination_reason", "not_started"),
                     "runtime_enabled": enabled,
                     "planning": enabled and runtime.get("planning", True),
                     "memory": enabled and runtime.get("memory", True),
                     "reflection": enabled and runtime.get("reflection", True)})
    return rows


def group_metrics(rows):
    started = [row for row in rows if row["started"]]
    measured = [row for row in started if row["usage_recorded"]]
    successful = sum(row["e2e_success"] for row in rows)
    completed = sum(row["execution_completed"] for row in rows)
    return {"planned": len(rows), "started": len(started), "not_started": len(rows) - len(started),
            "e2e_successes": successful, "e2e_success_rate": successful / len(rows) if rows else 0,
            "execution_completed": completed, "execution_completion_rate": completed / len(rows) if rows else 0,
            "blocked": sum(row["agent_verdict"] == "blocked" for row in rows),
            "infra_errors": sum(row["oracle_verdict"] == "infra_error" for row in rows),
            "mean_actions": average(started, "actions"), "mean_model_calls": average(started, "model_calls"),
            "mean_elapsed_seconds": average(started, "elapsed_seconds"),
            "usage_recorded": len(measured), "usage_missing": len(started) - len(measured),
            "prompt_tokens": sum(row["prompt_tokens"] for row in started),
            "completion_tokens": sum(row["completion_tokens"] for row in started),
            "generation_seconds": sum(row["generation_seconds"] for row in started),
            "mean_generation_seconds": average(measured, "generation_seconds")}


def paired_comparison(rows, groups, group, reference):
    target = {(row["case_id"], row["build"], row["workflow"], row["repeat"]): row
              for row in rows if row["group"] == group}
    baseline = {(row["case_id"], row["build"], row["workflow"], row["repeat"]): row
                for row in rows if row["group"] == reference}
    differences = []
    for key, row in target.items():
        if key not in baseline:
            continue
        other = baseline[key]
        observed = row["started"] and other["started"]
        difference = {"group": group, "reference": reference, "case_id": key[0], "build": key[1],
                      "workflow": key[2], "repeat": key[3], "both_started": observed,
                      "group_run_id": row["run_id"], "reference_run_id": other["run_id"],
                      "group_e2e_success": row["e2e_success"], "reference_e2e_success": other["e2e_success"],
                      "e2e_success_delta": int(row["e2e_success"]) - int(other["e2e_success"]) if observed else None}
        for name in ("actions", "model_calls", "elapsed_seconds", "prompt_tokens", "completion_tokens", "generation_seconds"):
            measured = observed and (name not in {"prompt_tokens", "completion_tokens", "generation_seconds"}
                                     or row["usage_recorded"] and other["usage_recorded"])
            difference[name + "_delta"] = row[name] - other[name] if measured else None
        differences.append(difference)
    observed = [row for row in differences if row["both_started"]]
    comparison = {"group": group, "reference": reference, "pairs_planned": len(differences),
                  "pairs_observed": len(observed),
                  "e2e_rate_difference_pp": 100 * (groups[group]["e2e_success_rate"] - groups[reference]["e2e_success_rate"]),
                  "wins": sum(row["e2e_success_delta"] == 1 for row in observed),
                  "losses": sum(row["e2e_success_delta"] == -1 for row in observed),
                  "ties": sum(row["e2e_success_delta"] == 0 for row in observed)}
    return comparison, differences


def average(rows, name):
    return mean(row[name] for row in rows) if rows else None


def write_csv(path, rows):
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]) if rows else ["group", "reference"])
        writer.writeheader()
        writer.writerows(rows)


def render_summary(summary, paired_rows):
    groups = summary["groups"]
    lines = [f"# {summary['experiment']}", "",
             f"计划 {summary['planned']} 次；已启动 {summary['started']} 次；未启动 {summary['not_started']} 次。", "",
             "E2E 成功要求 Agent 与独立判据均符合该任务预期，正确检出缺陷并输出 fail 计为成功。"
             "执行完成指独立判据为 pass 或 fail。未启动、blocked 和环境错误保留在计划分母。", ""]
    for title, identifiers in (("模型对照", ("base-full", "sft-full", "grpo-full")),
                               ("Harness 消融", ("grpo-full", "grpo-no-planning", "grpo-no-memory", "grpo-no-reflection"))):
        lines.extend([f"## {title}", "", "| 组 | E2E 成功 | 执行完成 | 未启动 | 环境错误 | 平均动作 | 平均调用 | 平均耗时(秒) | 输入/输出 tokens |",
                      "|---|---|---|---|---|---|---|---|---|"])
        for identifier in identifiers:
            if identifier not in groups:
                continue
            item = groups[identifier]
            lines.append(f"| {identifier} | {item['e2e_successes']}/{item['planned']} ({item['e2e_success_rate']:.1%}) | "
                         f"{item['execution_completed']}/{item['planned']} | {item['not_started']} | {item['infra_errors']} | "
                         f"{number(item['mean_actions'])} | {number(item['mean_model_calls'])} | "
                         f"{number(item['mean_elapsed_seconds'])} | {item['prompt_tokens']}/{item['completion_tokens']} |")
        lines.append("")
    lines.extend(["均值按已启动任务计算；tokens 与生成耗时来自全部模型角色的实际计数。"
                  "缺失计数在 CSV 中为 0，并由 usage_recorded 标记；以下列出已启动任务的计数覆盖。", ""])
    lines.extend(f"- {identifier}：{item['usage_recorded']}/{item['started']} 次有完整计数。" for identifier, item in groups.items())
    lines.extend(["", "## 配对比较", "", "差值方向为比较组减参考组；消融统一使用完整 GRPO Harness 减去关闭单项机制的组。", "",
                  "| 比较组 | 参考组 | E2E 差值(百分点) | 已观察配对 | 改善 | 退步 | 持平 |", "|---|---|---|---|---|---|---|"])
    for item in [*summary["model_comparisons"], *summary["ablations"]]:
        lines.append(f"| {item['group']} | {item['reference']} | {item['e2e_rate_difference_pp']:+.1f} | "
                     f"{item['pairs_observed']}/{item['pairs_planned']} | {item['wins']} | {item['losses']} | {item['ties']} |")
    lines.extend(["", "## 逐任务差异", "", "仅双方均已启动的配对计算差异；计数缺失时 token 差异留空。", "",
                  "| 比较组 / 参考组 | 任务 | 版本 | E2E Δ | 动作 Δ | 调用 Δ | 耗时 Δ(秒) | 输出 tokens Δ |",
                  "|---|---|---|---|---|---|---|---|"])
    for item in paired_rows:
        lines.append(f"| {item['group']} / {item['reference']} | {item['case_id']} · {item['workflow']} · {item['repeat']} | "
                     f"{item['build']} | {number(item['e2e_success_delta'])} | {number(item['actions_delta'])} | "
                     f"{number(item['model_calls_delta'])} | {number(item['elapsed_seconds_delta'])} | {number(item['completion_tokens_delta'])} |")
    lines.extend(["", "[逐次结果](results.csv) · [逐任务配对差异](paired-differences.csv) · [结构化汇总](summary.json)", ""])
    return "\n".join(lines)


def number(value):
    return "—" if value is None else f"{value:.2f}"
