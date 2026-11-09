"""Evaluate one adapter on the frozen expanded review test using two GPUs."""

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time

from guiqa.rl_review_validation import merge_validation


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gpus", default="0,1")
    args = parser.parse_args()
    gpus = args.gpus.split(",")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    jobs = [launch_shard(args.adapter.resolve(), output, gpu, index, len(gpus))
            for index, gpu in enumerate(gpus)]
    statuses = []
    for directory, process, log in jobs:
        status = process.wait()
        log.close()
        statuses.append(status)
        (directory / "exit.json").write_text(json.dumps({"exit_code": status}) + "\n")
    if any(statuses):
        (output / "exit.json").write_text(json.dumps({"exit_code": 1}) + "\n")
        raise SystemExit(1)
    summary = merge_validation(output, len(gpus))
    (output / "exit.json").write_text(json.dumps({"exit_code": 0}) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


def launch_shard(adapter, output, gpu, index, shards):
    directory = output / f"shard-{index}"
    directory.mkdir(parents=True, exist_ok=True)
    command = [str(ROOT / ".model-venv/bin/python"), "-u",
               str(ROOT / "scripts/evaluate_review_grpo.py"),
               "--adapter", str(adapter),
               "--dataset", str(ROOT / "datasets/review-final-test-expanded-v1"),
               "--split", "test", "--facts-only", "--shards", str(shards),
               "--shard-index", str(index), "--output", str(output)]
    environment = dict(os.environ, CUDA_VISIBLE_DEVICES=gpu, OMP_NUM_THREADS="4",
                       TOKENIZERS_PARALLELISM="false")
    log = (directory / "evaluate.log").open("a")
    process = subprocess.Popen(command, cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
                               stdout=log, stderr=subprocess.STDOUT)
    record = {"pid": process.pid, "host": socket.gethostname(), "gpu": gpu,
              "command": command, "started_at_epoch": time.time()}
    (directory / "launch.json").write_text(json.dumps(record, indent=2) + "\n")
    return directory, process, log


if __name__ == "__main__":
    main()
