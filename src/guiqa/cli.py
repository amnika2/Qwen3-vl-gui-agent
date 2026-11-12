import argparse
import json
import os
from pathlib import Path

import yaml

from .report import EXIT_CODES


def main(argv=None):
    args = arguments(argv)
    root = args.root.resolve()
    try:
        if args.command == "doctor":
            from .doctor import doctor
            result = doctor(root, load_config(root / args.config) if args.config else None)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ready"] else 3
        if args.command == "prepare":
            from .dataset import prepare
            sources = yaml.safe_load((root / "configs/sources.yaml").read_text())
            print(json.dumps(prepare(root, sources["dataset"]["revision"]), indent=2))
            return 0
        if args.command == "report":
            from .report import render
            print(render(args.run_dir.resolve()))
            return 0
        if args.command == "summarize":
            from .evaluate import summarize
            print(summarize(root, args.run_dir.resolve()))
            return 0
        if args.command == "status":
            from .tasks import status
            print(json.dumps(status(root, args.run_dir.resolve() if args.run_dir else None, args.limit),
                             ensure_ascii=False, indent=2))
            return 0
        if args.command == "tasks":
            from .tasks import create_session, run_tasks
            if args.resume:
                directory = args.resume.resolve()
                session = json.loads((directory / "session.json").read_text())
            else:
                directory, session = create_session(root, args.task_file.resolve(), args.config)
            model = load_model("qwen-controlled", session["config"])
            session = run_tasks(root, directory, session, model, args.port, args.retry_failed)
            print(json.dumps({"session_dir": str(directory), "status": session["status"],
                              "successful": session["successful"], "report": str(directory / "report.md")},
                             ensure_ascii=False, indent=2))
            return 130 if session["status"] == "interrupted" else (0 if session["successful"] else 4)
        config = load_config(root / args.config)
        if args.command == "run":
            from .runner import run_case
            model = load_model(args.method, config)
            directory, result = run_case(root, args.case, args.build, args.workflow, args.method, config,
                                         port=args.port, model=model, index=args.index)
            print(json.dumps({"run_dir": str(directory), **result}, ensure_ascii=False, indent=2))
            return 130 if result["termination_reason"] == "interrupted" else EXIT_CODES[result["oracle_verdict"]]
        from .evaluate import evaluate
        if args.command == "calibrate":
            suite = {"cases": [args.case], "repeats": 2, "split": "calibration"}
            methods = ["script"]
        else:
            suite = yaml.safe_load((root / args.suite).read_text())
            if config.get("agent_runtime", {}).get("enabled"):
                raise ValueError("stateful memory runs use 'run'; benchmark suites use a frozen config without agent_runtime")
            methods = args.methods.split(",")
            if not suite.get("cases") or type(suite.get("repeats")) is not int or suite["repeats"] < 1:
                raise ValueError("suite requires nonempty cases and positive integer repeats")
            if set(methods) - {"script", "sft", "qwen-base", "qwen-improved", "qwen-controlled"}:
                raise ValueError("available methods: script,sft,qwen-base,qwen-improved,qwen-controlled")
            if not suite.get("builds", ["A", "B"]) or set(suite.get("builds", ["A", "B"])) - {"A", "B"}:
                raise ValueError("suite builds must contain A and/or B")
        model = load_model("qwen-base", config) if any(method.startswith("qwen") for method in methods) else None
        directory, reports = evaluate(root, suite, methods, config, model=model, calibration=args.command == "calibrate", port=args.port)
        print(json.dumps({"experiment": str(directory), "reports": str(reports)}, indent=2))
        matrix = json.loads((directory / "matrix.json").read_text())
        return 0 if matrix["passed"] else 4
    except (ValueError, FileNotFoundError, yaml.YAMLError) as error:
        print(f"guiqa: {error}")
        return 3


def load_model(method, config):
    if method in ("script", "sft"):
        return None
    if not os.environ.get("CUDA_VISIBLE_DEVICES"):
        raise ValueError("set CUDA_VISIBLE_DEVICES to an allocated GPU")
    from .model import Qwen
    return Qwen(config)


def load_config(path):
    config = yaml.safe_load(path.read_text())
    for key in ("max_actions", "wall_seconds", "max_new_tokens", "max_pixels", "history_actions"):
        if type(config.get(key)) is not int or config[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    if type(config.get("seed")) is not int:
        raise ValueError("seed must be an integer")
    if config.get("review_format", "legacy") not in {"legacy", "evidence_v1", "evidence_v2", "evidence_facts_v1", "business_evidence_v1"}:
        raise ValueError("unsupported review_format")
    if (config.get("dataset") == "datasets/dataset26-v1") != (config.get("review_format") == "business_evidence_v1"):
        raise ValueError("dataset26 and business_evidence_v1 must be configured together")
    if "review_max_new_tokens" in config and (type(config["review_max_new_tokens"]) is not int or config["review_max_new_tokens"] < 1):
        raise ValueError("review_max_new_tokens must be a positive integer")
    config["model_path"] = str((path.parent.parent / config["model_path"]).resolve())
    if config.get("adapter_path"):
        config["adapter_path"] = str((path.parent.parent / config["adapter_path"]).resolve())
    if config.get("agent_runtime", {}).get("enabled"):
        settings = config["agent_runtime"]
        for key in ("planning", "memory", "reflection"):
            if key in settings and type(settings[key]) is not bool:
                raise ValueError(f"agent_runtime.{key} must be a boolean")
        for key in ("refresh_actions", "memory_top_k", "max_tool_calls"):
            if type(settings.get(key)) is not int or settings[key] < 1:
                raise ValueError(f"agent_runtime.{key} must be a positive integer")
        if not 1 <= settings["memory_top_k"] <= 10:
            raise ValueError("agent_runtime.memory_top_k must be between 1 and 10")
        if not isinstance(settings.get("memory_namespace"), str) or not settings["memory_namespace"]:
            raise ValueError("agent_runtime.memory_namespace is required")
        if settings.get("planner_adapter", "actor") not in {"base", "actor"}:
            raise ValueError("agent_runtime.planner_adapter must be base or actor")
        settings["memory_database"] = str((path.parent.parent / settings["memory_database"]).resolve())
        names = {"memory", "browser"}
        for server in settings.get("mcp_servers", []):
            if not server.get("name") or server["name"] in names:
                raise ValueError("MCP server names must be unique; memory and browser are reserved")
            names.add(server["name"])
            if ("url" in server) == ("command" in server):
                raise ValueError("each MCP server requires either url or command")
    return config


def arguments(argv):
    parser = argparse.ArgumentParser(prog="guiqa")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor").add_argument("--config", type=Path)
    commands.add_parser("prepare")
    status = commands.add_parser("status")
    status.add_argument("--run-dir", type=Path)
    status.add_argument("--limit", type=int, default=10)
    tasks = commands.add_parser("tasks")
    source = tasks.add_mutually_exclusive_group(required=True)
    source.add_argument("--task-file", type=Path)
    source.add_argument("--resume", type=Path)
    tasks.add_argument("--config", type=Path)
    tasks.add_argument("--retry-failed", action="store_true")
    tasks.add_argument("--port", type=int, default=13200)
    for command in ("report", "summarize"):
        commands.add_parser(command).add_argument("--run-dir", type=Path, required=True)
    calibrate = commands.add_parser("calibrate")
    calibrate.add_argument("--case", required=True)
    calibrate.add_argument("--config", type=Path, default=Path("configs/agent.yaml"))
    calibrate.add_argument("--port", type=int, default=13200)
    run = commands.add_parser("run")
    run.add_argument("--case", required=True)
    run.add_argument("--build", choices=["A", "B"], required=True)
    run.add_argument("--workflow", choices=["target", "preservation"], required=True)
    run.add_argument("--method", choices=["script", "sft", "qwen-base", "qwen-improved", "qwen-controlled"], required=True)
    run.add_argument("--config", type=Path, default=Path("configs/agent.yaml"))
    run.add_argument("--port", type=int, default=13200)
    run.add_argument("--index", type=int, default=0, help="zero-based dataset26 parameter instance")
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--suite", type=Path, required=True)
    evaluate.add_argument("--methods", default="script,qwen-base")
    evaluate.add_argument("--config", type=Path, default=Path("configs/agent.yaml"))
    evaluate.add_argument("--port", type=int, default=13200)
    args = parser.parse_args(argv)
    if args.command == "tasks" and args.resume and args.config:
        parser.error("tasks --resume uses the saved config; --config is only valid with --task-file")
    return args
