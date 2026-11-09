import json
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, ElementTree


EXIT_CODES = {"pass": 0, "fail": 1, "inconclusive": 2, "infra_error": 3}


def render(directory: Path):
    result = json.loads((directory / "result.json").read_text())
    lines = [f"# {result['case_id']} · {result['workflow']}", "",
             f"- 用例：{result.get('task', '')}", f"- 预期：{result.get('expected', '')}",
             f"- Agent 判定：{result['agent_verdict']}", f"- 独立判定：{result['oracle_verdict']}",
             f"- Agent 观察：{result.get('observation', '')}", f"- Agent 引用步骤：{result.get('evidence_steps', [])}",
             f"- 终止原因：{result['termination_reason']}", f"- 独立证据：{result.get('oracle_reason', '')}",
             f"- 独立读到的值：{result.get('actual')}", f"- 动作数：{result['actions']}；模型调用：{result['model_calls']}",
             f"- 耗时：{result['elapsed_seconds']:.2f} 秒", f"- 配置哈希：{result['config_hash']}",
             f"- 复跑：`{result.get('reproduce', '')}`", ""]
    if result.get("review_format") == "evidence_v1":
        lines.extend(["## 审查读值", "", "| 字段 | 任务要求 | 输入原文 / 步骤 | 结果原文 / 步骤 |",
                      "|---|---|---|---|"])
        for reading in result.get("field_readings", []):
            values = [reading["field"], reading["requested_value"],
                      f"{reading['input_value']} / {reading['input_step']}",
                      f"{reading['observed_value']} / {reading['observed_step']}"]
            lines.append("| " + " | ".join(str(value).replace("|", "\\|").replace("\n", "<br>")
                                           for value in values) + " |")
        lines.extend(["", f"- 提交证据步骤：{result.get('submission_step')}",
                      f"- 审查选图：{json.dumps(result.get('review_evidence', []), ensure_ascii=False)}", ""])
    elif result.get("review_format") in {"evidence_v2", "evidence_facts_v1"}:
        lines.extend(["## 审查读值", "", "| 字段 | 意图 | 任务要求 | 前态 | 结果 |",
                      "|---|---|---|---|---|"])
        for reading in result.get("field_readings", []):
            values = [reading[key] for key in ("field", "intent", "requested_value", "before", "after")]
            lines.append("| " + " | ".join(str(value).replace("|", "\\|").replace("\n", "<br>")
                                           for value in values) + " |")
        lines.extend(["", f"- 提交：{json.dumps(result.get('submission'), ensure_ascii=False)}",
                      f"- 审查选图：{json.dumps(result.get('review_evidence', []), ensure_ascii=False)}", ""])
    state_path = directory / "task-state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text())
        lines.extend(["## 任务状态与记忆", "", f"- 状态：{state['phase']}",
                      f"- 终止原因：{result['termination_reason']}",
                      f"- 下一步目标：{state['next_goal']}",
                      f"- 上下文更新：{state['context_updates']} 次",
                      f"- 已压缩至步骤：{state['compacted_through_step']}",
                      f"- 检索经验：{len(state['retrieved_experiences'])} 条",
                      f"- 历史摘要：{state['summary']}", f"- 最近反思：{state['reflection']}", ""])
        lines.extend(f"- [{step['status']}] {step['step']}" for step in state["plan"])
        unfinished = [step["step"] for step in state["plan"] if step["status"] != "done"]
        lines.extend(["", "待完成步骤：" + ("；".join(unfinished) if unfinished else "无"), ""])
        artifacts = [("任务状态", "task-state.json"), ("上下文更新", "context-events.jsonl"),
                     ("工具调用", "tool-calls.jsonl"), ("本次经验", "experience.json")]
        lines.extend([" · ".join(f"[{label}]({name})" for label, name in artifacts
                                if (directory / name).exists()), ""])
    lines.extend(["## 步骤证据", ""])
    trace = directory / "trace.jsonl"
    if trace.exists():
        for line in trace.read_text().splitlines():
            item = json.loads(line)
            lines.append(f"{item['step']}. {item.get('description', item.get('action', ''))}")
            if item.get("screenshot"):
                lines.append(f"\n![步骤 {item['step']}]({item['screenshot']})\n")
    (directory / "report.md").write_text("\n".join(lines) + "\n")
    suite = Element("testsuite", name="guiqa", tests="1", failures=str(int(result["oracle_verdict"] == "fail")),
                    errors=str(int(result["oracle_verdict"] in ("infra_error", "inconclusive"))))
    case = SubElement(suite, "testcase", name=f"{result['case_id']}:{result['workflow']}", time=str(result["elapsed_seconds"]))
    verdict = result["oracle_verdict"]
    if verdict != "pass":
        SubElement(case, "failure" if verdict == "fail" else "error", type=verdict,
                   message=result["termination_reason"]).text = result.get("error", result.get("oracle_reason", ""))
    ElementTree(suite).write(directory / "junit.xml", encoding="utf-8", xml_declaration=True)
    return directory / "report.md"
