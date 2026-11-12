"""Run a named list of business tasks and retain every attempt."""

from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

import yaml

from .artifacts import source_digest, write_json
from .runner import load_case, run_case


def create_session(root, task_file, config_path=None):
    from .cli import load_config

    specification = yaml.safe_load(task_file.read_text())
    validate_task_specification(specification)
    selected_config = root / (config_path or specification.get("config", "configs/agent-full-base.yaml"))
    config = load_config(selected_config)
    validate_task_cases(root, specification, config)
    session_id = "tasks-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    directory = root / "runs" / session_id
    directory.mkdir(parents=True)
    if config.get("agent_runtime", {}).get("enabled"):
        config["agent_runtime"].update(memory_database=str(directory / "memory.sqlite3"), memory_namespace=session_id)
    session = {"session_id": session_id, "name": specification.get("name", task_file.stem),
               "status": "pending", "config": config, "source_hash": source_digest(root),
               "tasks": [{**task, "attempts": []} for task in specification["tasks"]]}
    (directory / "tasks.yaml").write_bytes(task_file.read_bytes())
    write_json(directory / "session.json", session)
    render_session(directory, session)
    return directory, session


def run_tasks(root, directory, session, model, port=13200, retry_failed=False):
    session["status"] = "running"
    write_json(directory / "session.json", session)
    print(json.dumps({"session_dir": str(directory), "tasks": len(session["tasks"])}, ensure_ascii=False), flush=True)
    for task in session["tasks"]:
        previous = task["attempts"][-1] if task["attempts"] else None
        if previous and previous["termination_reason"] != "interrupted" and not (retry_failed and not previous["successful"]):
            continue
        session["current_task"] = task["id"]
        write_json(directory / "session.json", session)

        def started(run_directory):
            session["active_run_dir"] = str(run_directory)
            write_json(directory / "session.json", session)

        run_directory, result = run_case(
            root, task["case"], task["build"], task["workflow"], "qwen-controlled", session["config"],
            port=port, model=model, experiment=session["session_id"] + "/" + task["id"],
            repeat=len(task["attempts"]), on_started=started)
        expected = result["matrix_expected"]
        attempt = {"run_id": result["run_id"], "run_dir": str(run_directory),
                   "agent_verdict": result["agent_verdict"], "oracle_verdict": result["oracle_verdict"],
                   "termination_reason": result["termination_reason"],
                   "successful": result["agent_verdict"] == result["oracle_verdict"] == expected,
                   "actions": result["actions"], "model_calls": result["model_calls"],
                   "elapsed_seconds": result["elapsed_seconds"]}
        task["attempts"].append(attempt)
        session.pop("active_run_dir", None)
        print(json.dumps({"task": task["id"], **attempt}, ensure_ascii=False), flush=True)
        write_json(directory / "session.json", session)
        render_session(directory, session)
        if result["termination_reason"] == "interrupted":
            session["status"] = "interrupted"
            break
    else:
        session["status"] = "completed"
    session.pop("current_task", None)
    session["successful"] = all(task["attempts"] and task["attempts"][-1]["successful"] for task in session["tasks"])
    write_json(directory / "session.json", session)
    render_session(directory, session)
    return session


def validate_tasks(root, specification, config=None):
    validate_task_specification(specification)
    validate_task_cases(root, specification, config)


def validate_task_specification(specification):
    if not isinstance(specification, dict) or not isinstance(specification.get("tasks"), list) or not specification["tasks"]:
        raise ValueError("task file requires a nonempty tasks list")
    for key in ("name", "config"):
        if key in specification and (not isinstance(specification[key], str) or not specification[key]):
            raise ValueError(f"task file {key} must be a nonempty string")
    identifiers = set()
    for task in specification["tasks"]:
        if not isinstance(task, dict) or set(task) != {"id", "case", "build", "workflow"}:
            raise ValueError("each task requires exactly id, case, build and workflow")
        if not all(isinstance(value, str) and value for value in task.values()):
            raise ValueError("task fields must be nonempty strings")
        if task["id"] in identifiers:
            raise ValueError("task ids must be unique")
        identifiers.add(task["id"])
        if task["build"] not in {"A", "B"} or task["workflow"] not in {"target", "preservation"}:
            raise ValueError("task requires build A/B and workflow target/preservation")


def validate_task_cases(root, specification, config):
    if config is not None and config.get("dataset") == "datasets/dataset26-v1":
        plan = json.loads((root / config["dataset"] / "plan.json").read_text())
        applications = {app["case_id"] for app in plan["applications"]}
        for task in specification["tasks"]:
            if task["case"] not in applications:
                raise ValueError("task application is outside the frozen dataset26 plan: " + task["case"])
    else:
        for task in specification["tasks"]:
            load_case(root, task["case"], task["workflow"])


def render_session(directory, session):
    completed = sum(bool(task["attempts"]) for task in session["tasks"])
    successful = sum(bool(task["attempts"] and task["attempts"][-1]["successful"]) for task in session["tasks"])
    lines = [f"# {session['name']}", "", f"状态：{session['status']}",
             f"已执行：{completed}/{len(session['tasks'])}；业务验证成功：{successful}/{len(session['tasks'])}", "",
             "| 任务 | Agent | 独立判据 | 结束原因 | 尝试次数 | 报告 |", "|---|---|---|---|---|---|"]
    for task in session["tasks"]:
        if not task["attempts"]:
            lines.append(f"| {task['id']} | pending | — | — | 0 | — |")
            continue
        attempt = task["attempts"][-1]
        path = Path(attempt["run_dir"])
        lines.append(f"| {task['id']} | {attempt['agent_verdict']} | {attempt['oracle_verdict']} | "
                     f"{attempt['termination_reason']} | {len(task['attempts'])} | [查看](../{path.name}/report.md) |")
    (directory / "report.md").write_text("\n".join(lines) + "\n")


def status(root, directory=None, limit=10):
    if directory is None:
        paths = sorted((root / "runs").glob("*/result.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        sessions = list((root / "runs").glob("*/session.json"))
        paths = sorted([*paths, *sessions], key=lambda path: path.stat().st_mtime, reverse=True)[:limit]
        return [status(root, path.parent) for path in paths]
    session_path = directory / "session.json"
    if session_path.exists():
        session = json.loads(session_path.read_text())
        return {"session_dir": str(directory), "name": session["name"], "status": session["status"],
                "current_task": session.get("current_task"),
                "active_run": status(root, Path(session["active_run_dir"])) if session.get("active_run_dir") else None,
                "tasks": [{"id": task["id"], "attempts": len(task["attempts"]),
                           "latest": task["attempts"][-1] if task["attempts"] else None} for task in session["tasks"]],
                "report": str(directory / "report.md")}
    result = json.loads((directory / "result.json").read_text())
    state_path = directory / "task-state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else None
    return {"run_dir": str(directory), "result": result, "task_state": state,
            "report": str(directory / "report.md")}
