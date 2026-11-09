"""Launch review validation, live checks and frozen-trajectory re-review after selection."""

import json
import shlex
import subprocess
import tarfile
from pathlib import Path

from guiqa.sft_v2 import checksum, read_json, write_json


def main():
    root = Path(__file__).resolve().parents[1]
    completion = selected_training(root)
    directory = root / "runs/sft-v4-check-workers"
    directory.mkdir(exist_ok=False)
    files = sorted([*root.glob("src/guiqa/**/*.py"), *root.glob("scripts/*v4*.py"),
                    root / "scripts/validate_review_protocol.py", root / "configs/agent-review-evidence-v4.yaml"])
    with tarfile.open(directory / "check-source.tar.gz", "w:gz") as archive:
        for path in files:
            archive.add(path, arcname=str(path.relative_to(root)))
    write_json(directory / "check-source.json", {str(path.relative_to(root)): checksum(path) for path in files})
    workers = []
    python = str(root / ".model-venv/bin/python")
    config = "configs/agent-review-evidence-v4.yaml"
    for host, gpu in (("402", "0"), ("402", "1"), ("404", "2"), ("404", "3")):
        slot = host + "-gpu" + gpu
        if host == "402":
            commands = [[python, "-u", "scripts/review_sft_v4_holdout.py", "--config", config,
                         "--output", f"reports/sft-v4-review-holdout/shard-{shard}",
                         "--shard-index", str(shard), "--shards", "4"]
                        for shard in (int(gpu), int(gpu) + 2)]
        elif gpu == "2":
            commands = [[python, "-u", "scripts/validate_sft_v4_live.py", "--config", config,
                         "--scenario", scenario, "--port", str(port), "--output", "reports/sft-v4-live/v4-" + name]
                        for scenario, name, port in (("fc-lite-code-country", "lite", 13236),
                                                     ("fc-wolf-service-breadcrumb", "wolf", 13237))]
        else:
            commands = [[python, "-u", "scripts/validate_review_protocol.py", "--config", config,
                         "--dataset", "datasets/sft-main-10apps-v4", "--output", "reports/sft-v4-protocol/v4-new"]]
        stages = []
        for index, command in enumerate(commands):
            stages.extend([shlex.join(command), f"check_status_{index}=$?"])
        format_string = '{"exit_codes":[' + ",".join(["%s"] * len(commands)) + "]}\n"
        variables = " ".join(f'"$check_status_{index}"' for index in range(len(commands)))
        stages.append(f"printf {shlex.quote(format_string)} {variables} > "
                      + shlex.quote(str(directory / (slot + "-exit.json"))))
        command = ["env", "CUDA_VISIBLE_DEVICES=" + gpu, "OMP_NUM_THREADS=4", "TOKENIZERS_PARALLELISM=false",
                   "bash", "-c", "\n".join(stages)]
        script = (f"cd {shlex.quote(str(root))}\n"
                  f"nohup {shlex.join(command)} > {shlex.quote(str(directory / (slot + '.log')))} 2>&1 < /dev/null &\n"
                  "check_pid=$!\n"
                  "printf '%s\\n' \"$check_pid\"\n")
        result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ClearAllForwardings=yes", host, "bash", "-s"],
                                input=script, text=True, capture_output=True, check=True)
        worker = {"host": host, "gpu": gpu, "slot": slot, "pid": int(result.stdout.strip()), "commands": commands}
        workers.append(worker)
        write_json(directory / "launch.json", {"adapter_sha256": completion["adapter_sha256"], "workers": workers})
        print(json.dumps(worker), flush=True)


def selected_training(root):
    run = root / "runs/sft-qwen3vl-10apps-v4"
    result = read_json(run / "completion.json")
    if not result["completed"] or result["epoch"] != 2 or read_json(run / "train-exit.json")["exit_code"] != 0:
        raise ValueError("finish both training epochs before launching validation")
    if checksum(run / "adapter/adapter_model.safetensors") != result["adapter_sha256"]:
        raise ValueError("selected adapter differs from training completion")
    return result


if __name__ == "__main__":
    main()
