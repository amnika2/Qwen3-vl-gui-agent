"""Run one frozen browser collection lane, retaining its logs and subprocess exits."""

import argparse
import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runs/review-expansion-v1"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", type=int, choices=(0, 1, 2, 3, 4, 5, 6), required=True)
    args = parser.parse_args()
    plan = json.loads((RUNTIME / "core-plan.json").read_text())
    chosen = {0: ["core-php-main-404-a", "core-php-input-dev"], 1: ["core-opencart-main"],
              2: ["core-type-wolf"], 3: ["core-copy"], 4: ["core-php-main-404-b"],
              5: ["core-opencart-shard-0"], 6: ["core-opencart-shard-1"]}[args.lane]
    environment = dict(os.environ, LD_LIBRARY_PATH=str(ROOT / "vendor/runtime/root/usr/lib/x86_64-linux-gnu"))
    exits = []
    for name in chosen:
        batch = next(batch for batch in plan["batches"] if batch["name"] == name)
        directory = RUNTIME / batch["name"]
        script = "scripts/collect_sft_v4_curriculum.py" if batch["name"] == "core-copy" else "scripts/collect_rl_targeted.py"
        command = [str(ROOT / ".venv/bin/python"), "-u", script, "--catalog",
                   str(ROOT / "configs/review-expansion-v1" / batch["catalog"]), "--output", str(directory),
                   "--port", str(batch["port"]), *batch["flags"]]
        if (directory / "manifest.json").exists():
            command.append("--resume")
        result = subprocess.run(command, cwd=ROOT, env=environment)
        record = {"batch": batch["name"], "exit_code": result.returncode, "command": command}
        exits.append(record)
        (RUNTIME / f"{batch['name']}-exit.json").write_text(json.dumps(record, indent=2) + "\n")
    (RUNTIME / f"core-lane-{args.lane}-exit.json").write_text(json.dumps(exits, indent=2) + "\n")
    raise SystemExit(0 if all(record["exit_code"] == 0 for record in exits) else 4)


if __name__ == "__main__":
    main()
