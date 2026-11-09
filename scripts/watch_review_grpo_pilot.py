"""Evaluate saved GRPO checkpoints on the same 39-review greedy development set."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import subprocess
import time


ROOT = Path(__file__).resolve().parents[1]
ASSIGNMENTS = [(0, "402-remote", 1), (1, "local", 2), (2, "local", 3)]
ENVIRONMENT = {"OMP_NUM_THREADS": "4", "TOKENIZERS_PARALLELISM": "false",
               "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
               "PYTHONDONTWRITEBYTECODE": "1"}


def main():
    args = arguments()
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    try:
        wait_baseline(args)
        baseline = merge(args.baseline)
        records.append(metric_record("v4", 0, args.baseline, baseline))
        write_comparison(args, records)
        for step in args.steps:
            wait_checkpoint(args, step)
            evaluate_checkpoint(args, step)
            output = args.output / f"checkpoint-{step}"
            summary = merge(output)
            records.append(metric_record(f"checkpoint-{step}", step, output, summary))
            write_comparison(args, records)
        write_status(args, "completed", evaluated_steps=args.steps)
    except Exception as error:
        write_status(args, "failed", error=str(error))
        write_comparison(args, records, error=str(error))
        raise


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training", type=Path, default=ROOT / "runs/rl-review-grpo-pilot-v1")
    parser.add_argument("--baseline", type=Path, default=ROOT / "reports/rl-review-grpo-pilot-baseline-v1")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/rl-review-grpo-pilot-v1")
    parser.add_argument("--ops", type=Path, default=ROOT / "runs/rl-review-grpo-pilot-ops-v1")
    parser.add_argument("--steps", type=int, nargs="+", default=[10, 20, 30, 40, 50])
    parser.add_argument("--poll-seconds", type=float, default=10)
    args = parser.parse_args()
    if args.steps != sorted(set(args.steps)) or any(step < 1 for step in args.steps):
        parser.error("steps must be positive, unique and increasing")
    if not 0 < args.poll_seconds <= 60:
        parser.error("poll-seconds must be within (0, 60]")
    for key in ("training", "baseline", "output", "ops"):
        setattr(args, key, getattr(args, key).resolve())
    return args


def wait_baseline(args):
    workers = json.loads((args.ops / "baseline-launch.json").read_text())["workers"]
    write_status(args, "waiting_baseline", workers=workers)
    while any(existing_process_running(worker) for worker in workers):
        time.sleep(args.poll_seconds)
    for index, _, _ in ASSIGNMENTS:
        directory = args.baseline / f"shard-{index}"
        summary = json.loads((directory / "summary.json").read_text())
        if not summary["completed"] or not (directory / "runtime.json").exists():
            raise RuntimeError(f"baseline shard {index} exited without a complete evaluation")


def existing_process_running(worker):
    command = ["ps", "-p", str(worker["pid"]), "-o", "stat="]
    if worker["host_alias"] != "local":
        command = ["ssh", "-o", "BatchMode=yes", "-o", "ClearAllForwardings=yes",
                   worker["host_alias"], shlex.join(command)]
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode not in (0, 1):
        raise RuntimeError("cannot inspect baseline process: " + result.stderr.strip())
    state = result.stdout.strip()
    return bool(state) and not state.startswith("Z")


def wait_checkpoint(args, step):
    ready = args.training / f"checkpoint-{step}" / "ready.json"
    write_status(args, "waiting_checkpoint", step=step, ready=str(ready))
    while not ready.exists():
        if (args.training / "failure.json").exists():
            raise RuntimeError(f"training failed before checkpoint-{step}; see training failure.json")
        if (args.training / "completion.json").exists():
            raise RuntimeError(f"training finished without checkpoint-{step}/ready.json")
        time.sleep(args.poll_seconds)


def evaluate_checkpoint(args, step):
    processes, workers = [], []
    output = args.output / f"checkpoint-{step}"
    for index, host, gpu in ASSIGNMENTS:
        command = [str(ROOT / ".rl-venv/bin/python"), "-u", str(ROOT / "scripts/evaluate_review_grpo.py"),
                   "--adapter", str(args.training / f"checkpoint-{step}"),
                   "--dataset", str(ROOT / "datasets/rl-review-pilot-v2"),
                   "--output", str(output), "--shards", "3", "--shard-index", str(index)]
        updates = {**ENVIRONMENT, "CUDA_VISIBLE_DEVICES": str(gpu)}
        invocation = command
        if host != "local":
            remote = (f"import os; os.chdir({str(ROOT)!r}); "
                      f"os.execvpe({command[0]!r}, {command!r}, dict(os.environ, **{updates!r}))")
            invocation = ["ssh", "-o", "BatchMode=yes", "-o", "ClearAllForwardings=yes", host,
                          shlex.join([str(ROOT / ".rl-venv/bin/python"), "-c", remote])]
        log = args.ops / f"checkpoint-{step}-shard-{index}.log"
        with log.open("a") as handle:
            process = subprocess.Popen(invocation, cwd=ROOT, env=dict(os.environ, **updates),
                                       stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT)
        processes.append(process)
        workers.append({"shard_index": index, "host_alias": host, "gpu": gpu, "pid": process.pid,
                        "pid_kind": "ssh" if host != "local" else "evaluation",
                        "command": command, "invocation": invocation, "environment": updates,
                        "log": str(log)})
    write_status(args, "evaluating_checkpoint", step=step, workers=workers)
    while any(process.poll() is None for process in processes):
        time.sleep(args.poll_seconds)
    for worker, process in zip(workers, processes):
        worker["returncode"] = process.returncode
    write_json(args.ops / f"checkpoint-{step}-evaluations.json", workers)
    if any(process.returncode != 0 for process in processes):
        raise RuntimeError(f"checkpoint-{step} evaluation failed; see per-shard logs")


def merge(output):
    command = [str(ROOT / ".rl-venv/bin/python"), str(ROOT / "scripts/evaluate_review_grpo.py"),
               "--output", str(output), "--merge", "--shards", "3"]
    subprocess.run(command, cwd=ROOT, env=dict(os.environ, CUDA_VISIBLE_DEVICES="", OMP_NUM_THREADS="4"),
                   check=True, stdout=subprocess.DEVNULL)
    summary = json.loads((output / "summary.json").read_text())
    if not summary["completed"]:
        raise RuntimeError("merged development evaluation is incomplete: " + str(output))
    return summary


def metric_record(name, step, output, summary):
    metrics, blocked = summary["all"], summary["label"]["blocked"]
    return {"name": name, "step": step, "output": str(output),
            "samples": metrics["samples"], "terminal_correct": metrics["terminal_verdict_correct"],
            "strict_correct": metrics["strict_correct"],
            "normal_false_alarms": metrics["normal_false_alarms"], "normal_samples": metrics["normal_samples"],
            "defects_detected": metrics["defects_detected"], "defect_samples": metrics["defect_samples"],
            "blocked_correct": blocked["terminal_verdict_correct"], "blocked_samples": blocked["samples"],
            "format_errors": metrics["format_errors"], "truncated": metrics["truncated"],
            "numeric_rewards": metrics["numeric_rewards"], "summary": summary}


def write_comparison(args, records, error=None, stopped=None):
    completion = args.training / "completion.json"
    report = {"updated_utc": datetime.now(timezone.utc).isoformat(),
              "completed": len(records) == len(args.steps) + 1 and error is None and stopped is None,
              "training_stopped": stopped is not None,
              "training_stop": stopped,
              "planned_evaluation_steps": args.steps,
              "evaluated_steps": [row["step"] for row in records if row["step"]],
              "automatic_next_action": None if stopped is not None else "evaluate remaining ready checkpoints",
              "protocol": "39 independent development reviews, greedy decoding, no schema constraint",
              "comparison_scope": "Baseline v4 and checkpoints share this exact protocol; historical schema-constrained 40-case results use a different protocol.",
              "selected_adapter": "runs/sft-qwen3vl-10apps-v4-review-selected/adapter",
              "selection_changed": False, "records": records, "error": error,
              "training_completion": json.loads(completion.read_text()) if completion.exists() else None}
    write_json(args.output / "comparison.json", report)
    lines = ["同一批 39 条独立验证数据，greedy 解码，不加输出 schema 约束。",
             "", "本表内的 v4 和检查点可直接比较；此前带 schema 约束的 40 条留出结果使用不同协议。",
             "", "| 模型 | 终判正确 | 结构化正确 | 正常误报 | 缺陷检出 | blocked 正确 | 格式错误 | 截断 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in records:
        lines.append(f"| {row['name']} | {row['terminal_correct']}/{row['samples']} | "
                     f"{row['strict_correct']}/{row['samples']} | "
                     f"{row['normal_false_alarms']}/{row['normal_samples']} | "
                     f"{row['defects_detected']}/{row['defect_samples']} | "
                     f"{row['blocked_correct']}/{row['blocked_samples']} | "
                     f"{row['format_errors']} | {row['truncated']} |")
    lines.extend(["", "当前选用仍为 v4，脚本保留全部检查点和逐条回答，未自动替换配置。"])
    if stopped is not None:
        lines[:0] = [f"本轮训练已停止，实际完成 {stopped['last_recorded_optimizer_step']} 步，"
                     f"最后完整保存的检查点为 {stopped['last_complete_checkpoint']} 步。",
                     "", "训练和后续检查点评估队列已停止；现有权重、数据和日志保留，下一步不会自动启动。", ""]
    if error:
        lines.extend(["", "运行中断：" + error])
    (args.output / "comparison.md").write_text("\n".join(lines) + "\n")


def write_status(args, phase, **details):
    status = {"phase": phase, "updated_utc": datetime.now(timezone.utc).isoformat(), **details}
    write_json(args.ops / "watcher-status.json", status)
    write_json(args.output / "status.json", status)
    print(json.dumps(status, ensure_ascii=False), flush=True)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
