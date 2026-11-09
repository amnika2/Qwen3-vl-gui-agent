"""Run explicitly assigned v2/v3 evaluation workers sequentially on one GPU."""

import argparse
import os
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from guiqa.artifacts import write_json


def main():
    args = arguments()
    root = Path(__file__).resolve().parents[1]
    directory = root / "runs" / f"{args.experiment}-workers"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{args.slot}.queue.json"
    state = {"slot": args.slot, "hostname": args.hostname, "gpu": args.gpu,
             "experiment": args.experiment, "planned": args.worker, "completed": [],
             "current_worker": None, "exit_code": None}
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": args.gpu, "OMP_NUM_THREADS": "4",
           "TOKENIZERS_PARALLELISM": "false"}
    runtime = str(root / "vendor/runtime/root/usr/lib/x86_64-linux-gnu")
    env["LD_LIBRARY_PATH"] = runtime + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    write_json(path, state)
    for worker in args.worker:
        state["current_worker"] = worker
        write_json(path, state)
        started = datetime.now(timezone.utc).isoformat()
        command = [str(root / ".model-venv/bin/python"), str(root / "scripts/evaluate_sft_v3.py"),
                   "run", "--variant", worker["variant"], "--case", worker["case_id"],
                   "--port", str(worker["port"]), "--experiment", args.experiment]
        result = subprocess.run(command, cwd=root, env=env)
        state["completed"].append({**worker, "started_at": started,
                                   "finished_at": datetime.now(timezone.utc).isoformat(),
                                   "exit_code": result.returncode})
        state["current_worker"] = None
        state["exit_code"] = result.returncode if result.returncode else None
        write_json(path, state)
        if result.returncode:
            return result.returncode
    state["exit_code"] = 0
    write_json(path, state)
    return 0


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu", required=True, choices=["0", "1", "2", "3"])
    parser.add_argument("--slot", required=True)
    parser.add_argument("--worker", required=True, type=parse_worker, action="append",
                        help="variant:case_id:port, for example v2:sweeper-005:13260")
    parser.add_argument("--experiment", default="sft-lora-v3")
    args = parser.parse_args()
    args.hostname = socket.gethostname()
    allocated = {"server402": {"0", "1"}, "server404": {"2", "3"}}
    if args.gpu not in allocated.get(args.hostname, set()):
        parser.error("allocated GPUs are server402 0/1 and server404 2/3")
    return args


def parse_worker(value):
    try:
        variant, case_id, port = value.split(":")
        port = int(port)
    except ValueError as error:
        raise argparse.ArgumentTypeError("worker must be variant:case_id:integer_port") from error
    if variant not in {"v2", "v3"} or case_id not in {
        "sweeper-005", "sweeper-007", "sweeper-008", "sweeper-040", "sweeper-041"
    } or not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("worker requires v2/v3, a frozen-five case and a valid port")
    return {"variant": variant, "case_id": case_id, "port": port}


if __name__ == "__main__":
    raise SystemExit(main())
