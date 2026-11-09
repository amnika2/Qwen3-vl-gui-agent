"""Exercise planning, compaction and MCP memory across two real GUI tasks."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tarfile

from guiqa.artifacts import write_json
from guiqa.cli import load_config
from guiqa.model import Qwen
from guiqa.runner import run_case


ROOT = Path(__file__).resolve().parents[1]


def main():
    args = arguments()
    args.output.mkdir(parents=True, exist_ok=False)
    config = load_config(args.config)
    config["agent_runtime"]["memory_database"] = str(args.output / "memory.sqlite3")
    config["agent_runtime"]["memory_namespace"] = "runtime-validation"
    write_json(args.output / "config.json", config)
    snapshot(args.output)
    model = Qwen(config)
    episodes = []
    for workflow in ("target", "preservation"):
        directory, result = run_case(ROOT, "sweeper-008", "B", workflow, "qwen-controlled", config,
                                     port=args.port, model=model, experiment=args.output.name)
        episode = inspect_episode(directory, result)
        episodes.append(episode)
        print(json.dumps(episode, ensure_ascii=False), flush=True)
        write_json(args.output / "episodes.json", episodes)
    recalled = episodes[0]["run_id"] in episodes[1]["recalled_run_ids"]
    summary = {"completed": all(episode["completed"] for episode in episodes),
               "cross_task_memory_verified": recalled,
               "context_compaction_verified": any(episode["compacted_through_step"] is not None for episode in episodes),
               "mcp_verified": all(episode["mcp_recall_and_save"] for episode in episodes),
               "episodes": episodes,
               "adapter_sha256": hashlib.sha256((Path(config["adapter_path"]) / "adapter_model.safetensors").read_bytes()).hexdigest()}
    summary["passed"] = all(summary[key] for key in (
        "completed", "cross_task_memory_verified", "context_compaction_verified", "mcp_verified"))
    write_json(args.output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if summary["passed"] else 1


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/agent-full-grpo.yaml")
    parser.add_argument("--output", type=Path, default=ROOT / "reports" /
                        ("agent-runtime-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")))
    parser.add_argument("--port", type=int, default=13381)
    args = parser.parse_args()
    args.config, args.output = args.config.resolve(), args.output.resolve()
    return args


def inspect_episode(directory, result):
    state_path = directory / "task-state.json"
    if not state_path.exists():
        return {"run_id": result["run_id"], "run_directory": str(directory), "completed": False,
                "error": result.get("error", result["termination_reason"]),
                "recalled_run_ids": [], "compacted_through_step": None, "mcp_recall_and_save": False}
    state = json.loads(state_path.read_text())
    calls = [json.loads(line) for line in (directory / "tool-calls.jsonl").read_text().splitlines()]
    return {"run_id": result["run_id"], "run_directory": str(directory),
            "workflow": result["workflow"], "agent_verdict": result["agent_verdict"],
            "oracle_verdict": result["oracle_verdict"], "actual": result["actual"],
            "completed": (result["agent_verdict"] == result["oracle_verdict"] == "pass"
                          and state["phase"] == "finished" and result["actual"] == result["expected"]),
            "actions": result["actions"], "model_calls": result["model_calls"],
            "elapsed_seconds": result["elapsed_seconds"],
            "context_updates": state["context_updates"],
            "compacted_through_step": state["compacted_through_step"],
            "recalled_run_ids": [row["source_run_id"] for row in state["retrieved_experiences"]],
            "mcp_recall_and_save": all(any(row["name"] == name and not row["result"]["is_error"]
                                           for row in calls) for name in
                                       ("memory__search_experiences", "memory__save_experience"))}


def snapshot(directory):
    files = [*ROOT.glob("src/guiqa/**/*.py"), ROOT / "configs/agent-full-grpo.yaml",
             ROOT / "pyproject.toml", Path(__file__).resolve()]
    with tarfile.open(directory / "source.tar.gz", "w:gz") as archive:
        for path in sorted(files):
            archive.add(path, arcname=str(path.relative_to(ROOT)))
    write_json(directory / "source.json", {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                           for path in sorted(files)})


if __name__ == "__main__":
    raise SystemExit(main())
