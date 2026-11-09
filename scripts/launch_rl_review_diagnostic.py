"""Launch the two diagnostic workers with persistent logs and exit records."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile


ROOT = Path(__file__).resolve().parents[1]


def main():
    args = arguments()
    output = args.output.resolve()
    runtime = args.runtime.resolve()
    if args.worker is not None:
        command = [sys.executable, "-u", "scripts/diagnose_rl_reviews.py", "worker",
                   "--output", str(output), "--shard-index", str(args.worker), "--shards", "2"]
        result = subprocess.run(command, cwd=ROOT)
        write_json(runtime / f"worker-{args.worker}-exit.json", {"exit_code": result.returncode})
        raise SystemExit(result.returncode)

    manifest = json.loads((output / "manifest.json").read_text())
    runtime.mkdir(parents=True, exist_ok=False)
    snapshot_sources(runtime)
    workers = []
    for index, gpu in enumerate((2, 3)):
        command = [str(ROOT / ".model-venv/bin/python"), "-u", str(Path(__file__).resolve()),
                   "--output", str(output), "--runtime", str(runtime), "--worker", str(index)]
        environment = dict(os.environ, CUDA_VISIBLE_DEVICES=str(gpu), OMP_NUM_THREADS="4",
                           TOKENIZERS_PARALLELISM="false")
        with (runtime / f"worker-{index}.log").open("w") as log:
            process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log,
                                       stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                       start_new_session=True)
        workers.append({"shard": index, "gpu": gpu, "pid": process.pid, "command": command})
        write_json(runtime / "launch.json", {"diagnostic_output": str(output),
                   "manifest_sha256": checksum(output / "manifest.json"),
                   "generation": manifest.get("generation"), "workers": workers})
        print(json.dumps(workers[-1]), flush=True)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/rl-review-diagnostic-v1")
    parser.add_argument("--runtime", type=Path, default=ROOT / "runs/rl-review-pilot-v1")
    parser.add_argument("--worker", type=int, choices=(0, 1))
    return parser.parse_args()


def snapshot_sources(runtime):
    source_names = ["scripts/launch_rl_review_diagnostic.py", "scripts/diagnose_rl_reviews.py",
                    "src/guiqa/rl_review_diagnostic.py", "src/guiqa/rl_review_reward.py",
                    "src/guiqa/model.py", "src/guiqa/review_contract.py", "src/guiqa/cli.py",
                    "configs/agent-review-evidence-v4-selected.yaml", "configs/sft-split.yaml"]
    with tarfile.open(runtime / "diagnostic-source.tar.gz", "w:gz") as archive:
        for name in source_names:
            archive.add(ROOT / name, arcname=name)
    write_json(runtime / "diagnostic-source.json", {name: checksum(ROOT / name) for name in source_names})
    preserved = ["datasets/sft-main-10apps-v3/train.jsonl", "datasets/sft-main-10apps-v3/validation.jsonl",
                 "datasets/sft-main-10apps-v4/train.jsonl", "datasets/sft-main-10apps-v4/validation.jsonl",
                 "runs/sft-qwen3vl-10apps-v4-review-selected/adapter/adapter_model.safetensors",
                 "runs/sft-qwen3vl-10apps-v4-review-selected/selection.json"]
    write_json(runtime / "preserved-sources.json", {name: checksum(ROOT / name) for name in preserved})


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
