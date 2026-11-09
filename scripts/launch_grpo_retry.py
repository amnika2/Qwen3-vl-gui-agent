"""Launch a short GRPO retry while preserving the existing development protocol."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENT = {"OMP_NUM_THREADS": "4", "TOKENIZERS_PARALLELISM": "false",
               "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
               "PYTHONDONTWRITEBYTECODE": "1"}


def main():
    args = arguments()
    baseline = verify_baseline(args)
    train = [str(ROOT / ".rl-venv/bin/python"), "-u", str(ROOT / "scripts/train_review_grpo_pilot.py"),
             "--config", str(args.config), "--output", str(args.training)]
    watcher = [str(ROOT / ".rl-venv/bin/python"), "-u", str(ROOT / "scripts/watch_review_grpo_pilot.py"),
               "--training", str(args.training), "--baseline", str(args.baseline),
               "--output", str(args.output), "--ops", str(args.ops),
               "--steps", *(str(step) for step in args.steps)]
    record = {"training_host": args.host, "training_gpu": args.gpu,
              "train_command": train, "watcher_command": watcher,
              "evaluation_rules": str(ROOT / "configs/rl-review-reward-rules.yaml"),
              "evaluation_rules_sha256": baseline["rules_sha256"],
              "baseline_manifest_sha256": file_hash(args.baseline / "manifest.json"),
              "environment": ENVIRONMENT}
    if args.dry_run:
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return
    args.ops.mkdir(parents=True)
    shutil.copyfile(args.baseline_ops / "baseline-launch.json", args.ops / "baseline-launch.json")
    updates = {**ENVIRONMENT, "CUDA_VISIBLE_DEVICES": str(args.gpu)}
    train_log = args.ops / "train.log"
    remote = (
        "import json,os,subprocess; "
        f"os.chdir({str(ROOT)!r}); "
        f"handle=open({str(train_log)!r},'w'); "
        f"process=subprocess.Popen({train!r},cwd={str(ROOT)!r},env=dict(os.environ,**{updates!r}),"
        "stdin=subprocess.DEVNULL,stdout=handle,stderr=subprocess.STDOUT,start_new_session=True); "
        "print(json.dumps({'pid':process.pid}))")
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ClearAllForwardings=yes", args.host,
         shlex.join([str(ROOT / ".rl-venv/bin/python"), "-c", remote])],
        check=True, capture_output=True, text=True)
    record["training_pid"] = json.loads(result.stdout)["pid"]
    record["train_log"] = str(train_log)
    with (args.ops / "watcher.log").open("w") as handle:
        process = subprocess.Popen(watcher, cwd=ROOT,
            env=dict(os.environ, **ENVIRONMENT, CUDA_VISIBLE_DEVICES=""),
            stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
    record["watcher_pid"] = process.pid
    record["watcher_log"] = str(args.ops / "watcher.log")
    (args.ops / "launch.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(record, ensure_ascii=False, indent=2))


def verify_baseline(args):
    manifest = json.loads((args.baseline / "manifest.json").read_text())
    summary = json.loads((args.baseline / "summary.json").read_text())
    if not summary["completed"] or manifest["samples"] != 39:
        raise ValueError("retry requires the complete original 39-review development baseline")
    for name, digest in manifest["source_sha256"].items():
        if file_hash(ROOT / name) != digest:
            raise ValueError("development evaluation source differs from the baseline: " + name)
    if file_hash(ROOT / "configs/rl-review-reward-rules.yaml") != manifest["rules_sha256"]:
        raise ValueError("development reward rules differ from the original baseline")
    settings = yaml.safe_load(args.config.read_text())
    dataset = ROOT / settings["dataset"]
    for name, digest in manifest["dataset_sha256"].items():
        if file_hash(dataset / name) != digest:
            raise ValueError("retry dataset differs from the original development baseline: " + name)
    config_path = ROOT / settings["model_config"]
    if file_hash(config_path) != manifest["config_sha256"]:
        raise ValueError("retry must retain the original selected-v4 model configuration")
    config = yaml.safe_load(config_path.read_text())
    for name, digest in manifest["adapter_sha256"].items():
        if file_hash(ROOT / config["adapter_path"] / name) != digest:
            raise ValueError("retry must start from the preserved v4 adapter: " + name)
    if args.training.exists() or args.output.exists() or args.ops.exists():
        raise FileExistsError("retry training, report and operations directories must be new")
    if args.steps[-1] != settings["max_steps"] or any(step % settings["save_steps"] for step in args.steps):
        raise ValueError("evaluation steps must match saved checkpoints and the fixed training budget")
    return manifest


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--training", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ops", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, default=ROOT / "reports/rl-review-grpo-pilot-baseline-v1")
    parser.add_argument("--baseline-ops", type=Path, default=ROOT / "runs/rl-review-grpo-pilot-ops-v1")
    parser.add_argument("--steps", type=int, nargs="+", default=[5, 10])
    parser.add_argument("--host", default="402-remote")
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.steps != sorted(set(args.steps)) or not args.steps or any(step < 1 for step in args.steps):
        parser.error("steps must be positive, unique and increasing")
    for key in ("config", "training", "output", "ops", "baseline", "baseline_ops"):
        setattr(args, key, getattr(args, key).resolve())
    return args


if __name__ == "__main__":
    main()
