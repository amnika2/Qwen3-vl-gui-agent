"""Launch the four explicitly allocated queues for the matched v2/v3 evaluation."""

import argparse
import json
import shlex
import subprocess
from pathlib import Path

from guiqa.artifacts import write_json


QUEUES = [
    ("402", "server402", "0", ["v2:sweeper-005:13260", "v3:sweeper-005:13261"]),
    ("402", "server402", "1", ["v2:sweeper-040:13266", "v3:sweeper-040:13267",
                                "v2:sweeper-041:13268", "v3:sweeper-041:13269"]),
    ("404", "server404", "2", ["v2:sweeper-007:13262", "v3:sweeper-007:13263"]),
    ("404", "server404", "3", ["v2:sweeper-008:13264", "v3:sweeper-008:13265"]),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", default="sft-lora-v3")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    directory = root / "runs" / f"{args.experiment}-workers"
    directory.mkdir(parents=True, exist_ok=False)
    launched = []
    for alias, hostname, gpu, workers in QUEUES:
        slot = f"{alias}-gpu{gpu}"
        command = [str(root / ".model-venv/bin/python"), str(root / "scripts/run_sft_eval_v3_queue.py"),
                   "--gpu", gpu, "--slot", slot, "--experiment", args.experiment]
        for worker in workers:
            command.extend(["--worker", worker])
        log, pid_file = directory / f"{slot}.log", directory / f"{slot}.pid"
        script = (f"cd {shlex.quote(str(root))}\n"
                  f"nohup {shlex.join(command)} > {shlex.quote(str(log))} 2>&1 < /dev/null &\n"
                  "evaluation_pid=$!\n"
                  f"printf '%s\\n' \"$evaluation_pid\" > {shlex.quote(str(pid_file))}\n"
                  "printf '%s\\n' \"$evaluation_pid\"\n")
        launch = ["ssh", "-o", "BatchMode=yes", "-o", "ClearAllForwardings=yes", alias, "bash", "-s"]
        result = subprocess.run(launch, input=script, text=True, capture_output=True, check=True)
        record = {"host": hostname, "gpu": gpu, "slot": slot, "pid": int(result.stdout.strip()),
                  "workers": workers, "command": command, "log": str(log.relative_to(root))}
        launched.append(record)
        write_json(directory / "launch.json", {"experiment": args.experiment, "queues": launched,
                                               "planned_runs": 80, "variants": ["v2", "v3"]})
        print(json.dumps({key: record[key] for key in ("host", "gpu", "slot", "pid", "workers")}), flush=True)


if __name__ == "__main__":
    main()
