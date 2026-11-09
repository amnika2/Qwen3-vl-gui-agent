"""Prepare, launch on 402 GPU 0/1, resume, and verify the fixed review SFT run."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
import time

import yaml


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".model-venv/bin/python"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ClearAllForwardings=yes", "402"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "launch", "detach", "worker", "verify"))
    parser.add_argument("--config", type=Path, default=ROOT / "configs/sft-train-review-v5.yaml")
    parser.add_argument("--resume-from", type=Path)
    parser.add_argument("--attempt", type=int, default=1)
    args = parser.parse_args()
    args.config = args.config.resolve()
    config = yaml.safe_load(args.config.read_text())
    directory = ROOT / config["output_dir"]
    if args.mode == "prepare":
        prepare(args, config, directory)
    elif args.mode == "launch":
        command = [str(PYTHON), "-u", str(Path(__file__).resolve()), "detach", "--config", str(args.config)]
        if args.resume_from:
            command += ["--resume-from", str(args.resume_from.resolve())]
        result = subprocess.run(SSH + [shlex.join(command)], text=True, check=True, capture_output=True)
        print(result.stdout, end="", flush=True)
    elif args.mode == "detach":
        detach(args, config, directory)
    elif args.mode == "worker":
        train(args, directory)
    else:
        print(json.dumps(verify_completion(config, directory), ensure_ascii=False, indent=2))


def prepare(args, config, directory):
    if config["epochs"] != 2 or config["load_best_model_at_end"] or config["save_strategy"] != "epoch":
        raise ValueError("review v5 uses exactly two epochs, both saved, with the final epoch exported")
    data = ROOT / config["dataset"]
    manifest = json.loads((data / "manifest.json").read_text())
    if (manifest["train_samples"], manifest["validation_samples"]) != (290, 39):
        raise ValueError("review v5 requires the frozen 290/39 dataset")
    preflight = ROOT / "reports/sft-review-v5-preflight/input-check.json"
    plan = {
        "training_started": False, "host": "402", "gpus": [0, 1],
        "config": config, "config_file": str(args.config),
        "expected_optimizer_steps": 38, "expected_effective_batch_size": 16,
        "selection": "Fixed second epoch; no selection by the 39 development answers.",
        "files_sha256": input_hashes(args.config, config),
        "preflight": str(preflight), "preflight_sha256": checksum(preflight),
        "initialization": "v4 language adapter with a fresh optimizer and scheduler",
        "recovery": "Use launch --resume-from runs/sft-qwen3vl-review-v5/checkpoint-N; restores optimizer, scheduler, Trainer state and both RNG states.",
        "checkpoint_budget": "Epochs 1 and 2 only; a failure does not increase this budget.",
    }
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "plan.json", plan)
    print(json.dumps({"prepared": str(directory), "training_started": False,
                      "host": "402", "gpus": [0, 1], "expected_optimizer_steps": 38}), flush=True)


def detach(args, config, directory):
    if subprocess.check_output(["hostname"], text=True).strip() != "server402":
        raise ValueError("review v5 must run on server402")
    plan = json.loads((directory / "plan.json").read_text())
    if input_hashes(args.config, config) != plan["files_sha256"]:
        raise ValueError("config, data or initial v4 adapter changed after preparation")
    attempts = sorted(directory.glob("launch-attempt-*.json"))
    if args.resume_from:
        args.resume_from = args.resume_from.resolve()
        if args.resume_from.parent != directory.resolve():
            raise ValueError("resume from a checkpoint of this same v5 run")
        checkpoint_state(args.resume_from)
        if not (directory / "training_manifest.json").exists():
            raise ValueError("resume requires the original v5 training manifest")
    elif attempts:
        raise ValueError("an existing v5 attempt must be resumed explicitly")
    waiting = busy_compute_processes()
    while waiting:
        print(json.dumps({"waiting_for_gpu_processes": waiting}), file=sys.stderr, flush=True)
        time.sleep(15)
        waiting = busy_compute_processes()
    attempt = len(attempts) + 1
    snapshot_source(directory, attempt)
    command = [str(PYTHON), "-u", str(Path(__file__).resolve()), "worker", "--config", str(args.config),
               "--attempt", str(attempt)]
    if args.resume_from:
        command += ["--resume-from", str(args.resume_from)]
    environment = dict(os.environ, CUDA_VISIBLE_DEVICES="0,1", OMP_NUM_THREADS="4",
                       TOKENIZERS_PARALLELISM="false")
    with (directory / "train.log").open("a" if attempts else "w") as log:
        process = subprocess.Popen(command, cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    record = {"attempt": attempt, "host": "402", "gpus": [0, 1], "pid": process.pid,
              "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "command": command, "resume_from": str(args.resume_from) if args.resume_from else None,
              "environment": {key: environment[key] for key in
                              ("CUDA_VISIBLE_DEVICES", "OMP_NUM_THREADS", "TOKENIZERS_PARALLELISM")},
              "files_sha256": plan["files_sha256"]}
    write_json(directory / f"launch-attempt-{attempt:03d}.json", record)
    write_json(directory / "launch.json", record)
    (directory / "train.pid").write_text(str(process.pid) + "\n")
    print(json.dumps(record), flush=True)


def train(args, directory):
    command = [str(PYTHON), "-u", "-m", "torch.distributed.run", "--standalone", "--nproc_per_node=2",
               "-m", "guiqa.train_sft", "--config", str(args.config)]
    if args.resume_from:
        command += ["--resume-from", str(args.resume_from)]
    result = subprocess.run(command, cwd=ROOT)
    exit_record = {"attempt": args.attempt, "exit_code": result.returncode,
                   "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "command": command}
    write_json(directory / f"train-exit-attempt-{args.attempt:03d}.json", exit_record)
    write_json(directory / "train-exit.json", exit_record)
    raise SystemExit(result.returncode)


def busy_compute_processes():
    output = subprocess.check_output([
        "nvidia-smi", "--query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory", "--format=csv,noheader"],
        text=True).strip()
    return output.splitlines() if output else []


def input_hashes(config_path, config):
    files = [config_path]
    files += [ROOT / config["dataset"] / name for name in
              ("manifest.json", "train.jsonl", "validation.jsonl", "media_manifest.json", "verification.json")]
    files += [ROOT / config["init_adapter_path"] / name for name in
              ("adapter_config.json", "adapter_model.safetensors")]
    return {str(path.relative_to(ROOT)): checksum(path) for path in files}


def snapshot_source(directory, attempt):
    files = sorted([*ROOT.glob("src/guiqa/**/*.py"), Path(__file__).resolve(),
                    ROOT / "configs/sft-train-review-v5.yaml", ROOT / "configs/sft-split.yaml",
                    ROOT / "configs/sources.yaml", ROOT / "docs/sft-review-v5-pixel-audit.json"])
    stem = "training-source" if attempt == 1 else f"training-source-resume-{attempt:03d}"
    with tarfile.open(directory / (stem + ".tar.gz"), "w:gz") as archive:
        for path in files:
            archive.add(path, arcname=str(path.relative_to(ROOT)))
    write_json(directory / (stem + ".json"), {str(path.relative_to(ROOT)): checksum(path) for path in files})


def checkpoint_state(path):
    files = ("adapter_config.json", "adapter_model.safetensors", "optimizer.pt", "scheduler.pt",
             "trainer_state.json", "rng_state_0.pth", "rng_state_1.pth")
    missing = [name for name in files if not (path / name).is_file()]
    if missing:
        raise ValueError("checkpoint is missing recovery state: " + ", ".join(missing))
    return json.loads((path / "trainer_state.json").read_text())


def verify_completion(config, directory):
    completion = json.loads((directory / "completion.json").read_text())
    exit_record = json.loads((directory / "train-exit.json").read_text())
    if (not completion["completed"] or exit_record["exit_code"] != 0
            or completion["epoch"] != 2 or completion["optimizer_steps"] != 38
            or completion["world_size"] != 2 or completion["effective_batch_size"] != 16
            or completion["train_samples"] != 290 or completion["validation_samples"] != 39
            or completion["vision_trainable_parameters"] != 0 or completion["best_checkpoint"] is not None):
        raise ValueError("training did not complete the fixed review v5 run")
    checkpoints = sorted(directory.glob("checkpoint-*"), key=lambda path: int(path.name.split("-")[-1]))
    states = [checkpoint_state(path) for path in checkpoints]
    if [state["epoch"] for state in states] != [1.0, 2.0]:
        raise ValueError("both epoch checkpoints must be retained")
    if checksum(directory / "adapter/adapter_model.safetensors") != completion["adapter_sha256"]:
        raise ValueError("exported adapter differs from completion")
    plan = json.loads((directory / "plan.json").read_text())
    config_path = Path(plan["config_file"])
    if input_hashes(config_path, config) != plan["files_sha256"]:
        raise ValueError("preserved config, dataset or initial v4 adapter changed")
    result = {"passed": True, "exit_code": 0, "optimizer_steps": 38, "epochs": 2,
              "adapter_sha256": completion["adapter_sha256"],
              "checkpoints": [{"path": str(path), "step": state["global_step"], "epoch": state["epoch"]}
                              for path, state in zip(checkpoints, states)],
              "optimizer_scheduler_rng_retained": True, "source_v4_and_data_preserved": True,
              "final_epoch_exported": True, "training_only_host": "402", "gpus": [0, 1]}
    write_json(directory / "completion-verification.json", result)
    return result


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
