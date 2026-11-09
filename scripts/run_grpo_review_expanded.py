"""Launch the expanded review GRPO run and evaluate its final adapter."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tarfile
import time

import yaml

from guiqa.artifacts import write_json


ROOT = Path(__file__).resolve().parents[1]
TRAIN_PYTHON = ROOT / ".rl-venv/bin/python"
EVAL_PYTHON = ROOT / ".model-venv/bin/python"
SFT_TEST = ROOT / "reports/sft-review-expanded-v1/expanded"
METRICS = ("macro_f1", "terminal_verdict_accuracy", "strict_accuracy", "defect_recall",
           "normal_false_alarm_rate", "defect_false_pass_rate")


def main():
    args = arguments()
    if args.mode == "launch":
        launch(args)
    else:
        raise SystemExit(worker(args))


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("launch", "worker"))
    parser.add_argument("--config", type=Path, default=ROOT / "configs/rl-review-grpo-expanded-v1.yaml")
    parser.add_argument("--output", type=Path, default=ROOT / "runs/rl-review-grpo-expanded-v1")
    parser.add_argument("--ops", type=Path, default=ROOT / "runs/rl-review-grpo-expanded-v1-ops")
    parser.add_argument("--report", type=Path, default=ROOT / "reports/rl-review-grpo-expanded-v1")
    parser.add_argument("--train-gpu", choices=("2", "3"), default="2")
    parser.add_argument("--eval-gpu", choices=("2", "3"), default="3")
    parser.add_argument("--resume-from", type=Path)
    args = parser.parse_args()
    if args.train_gpu == args.eval_gpu:
        parser.error("training and evaluation require different GPUs")
    for key in ("config", "output", "ops", "report", "resume_from"):
        if getattr(args, key) is not None:
            setattr(args, key, getattr(args, key).resolve())
    return args


def launch(args):
    args.ops.mkdir(parents=True, exist_ok=args.resume_from is not None)
    attempt = len(list(args.ops.glob("launch-attempt-*.json"))) + 1
    settings = yaml.safe_load(args.config.read_text())
    snapshot_sources(args, settings, attempt)
    command = [sys.executable, "-u", str(Path(__file__).resolve()), "worker"]
    for option in ("config", "output", "ops", "report", "train_gpu", "eval_gpu"):
        command += ["--" + option.replace("_", "-"), str(getattr(args, option))]
    if args.resume_from:
        command += ["--resume-from", str(args.resume_from)]
    log_path = args.ops / f"worker-attempt-{attempt:03d}.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    record = {"attempt": attempt, "pid": process.pid, "host": socket.gethostname(),
              "train_gpu": args.train_gpu, "eval_gpu": args.eval_gpu,
              "command": command, "resume_from": str(args.resume_from) if args.resume_from else None,
              "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "worker_log": str(log_path), "training_log": str(args.ops / "train.log"),
              "report": str(args.report)}
    write_json(args.ops / f"launch-attempt-{attempt:03d}.json", record)
    write_json(args.ops / "launch.json", record)
    print(json.dumps(record, ensure_ascii=False, indent=2), flush=True)


def worker(args):
    settings = yaml.safe_load(args.config.read_text())
    model_config = yaml.safe_load((ROOT / settings["model_config"]).read_text())
    sft_adapter = ROOT / model_config["adapter_path"]
    verify_reused_test(sft_adapter)
    train_command = [str(TRAIN_PYTHON), "-u", "-m", "guiqa.train_review_grpo_expanded",
                     "--config", str(args.config), "--output", str(args.output)]
    if args.resume_from:
        train_command += ["--resume-from", str(args.resume_from)]
    training = start_job(train_command, args.train_gpu, args.ops / "train.log", args.ops / "training")
    baseline = start_evaluation(sft_adapter, ROOT / settings["dataset"], "validation",
                                args.report / "sft/validation", args.eval_gpu)
    baseline_status = finish_job(baseline)
    training_status = finish_job(training)
    write_json(args.output / "train-exit.json", {"exit_code": training_status})
    if training_status or baseline_status:
        write_json(args.ops / "exit.json", {"exit_code": 1, "stage": "training_and_sft_validation"})
        return 1
    for partition, dataset in (("validation", ROOT / settings["dataset"]),
                               ("test", ROOT / "datasets/review-final-test-expanded-v1")):
        status = finish_job(start_evaluation(args.output / "adapter", dataset, partition,
                                             args.report / "grpo" / partition, args.eval_gpu))
        if status:
            write_json(args.ops / "exit.json", {"exit_code": status, "stage": partition})
            return status
    validation = compare_reviews(args.report / "sft/validation", args.report / "grpo/validation")
    test = compare_reviews(SFT_TEST, args.report / "grpo/test")
    completion = json.loads((args.output / "completion.json").read_text())
    result = {"completed": completion["completed"] and validation["completed"] and test["completed"],
              "selection": "Fixed final training adapter; test answers do not select checkpoints.",
              "training": completion, "validation": validation, "test": test}
    write_json(args.report / "comparison.json", result)
    write_json(args.ops / "exit.json", {"exit_code": 0, "stage": "completed"})
    print(json.dumps({"completed": result["completed"], "test_delta": test["delta"]}, indent=2), flush=True)
    return 0


def start_evaluation(adapter, dataset, partition, directory, gpu):
    command = [str(EVAL_PYTHON), "-u", str(ROOT / "scripts/evaluate_review_grpo.py"),
               "--adapter", str(adapter), "--dataset", str(dataset), "--split", partition,
               "--facts-only", "--output", str(directory)]
    return start_job(command, gpu, directory / "evaluate.log", directory)


def start_job(command, gpu, log_path, directory):
    directory.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, CUDA_VISIBLE_DEVICES=gpu, OMP_NUM_THREADS="4",
                       TOKENIZERS_PARALLELISM="false", PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True")
    log = log_path.open("a")
    process = subprocess.Popen(command, cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
                               stdout=log, stderr=subprocess.STDOUT)
    write_json(directory / "launch.json", {"pid": process.pid, "host": socket.gethostname(), "gpu": gpu,
               "command": command, "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    return directory, process, log


def finish_job(job):
    directory, process, log = job
    status = process.wait()
    log.close()
    write_json(directory / "exit.json", {"exit_code": status,
               "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    return status


def verify_reused_test(adapter):
    manifest = json.loads((SFT_TEST / "manifest.json").read_text())
    summary = json.loads((SFT_TEST / "summary.json").read_text())
    if not summary["completed"] or manifest["samples"] != 150:
        raise ValueError("the reusable SFT test baseline must contain all 150 answers")
    files = {ROOT / name: value for name, value in manifest["source_sha256"].items()}
    files.update({adapter / name: value for name, value in manifest["adapter_sha256"].items()})
    files.update({Path(manifest["dataset"]) / name: value
                  for name, value in manifest["dataset_sha256"].items()})
    files[ROOT / "configs/rl-review-reward-rules.yaml"] = manifest["rules_sha256"]
    files[ROOT / "configs/agent-review-evidence-v4-selected.yaml"] = manifest["config_sha256"]
    for path, expected in files.items():
        if file_hash(path) != expected:
            raise ValueError("reused SFT evaluation input changed: " + str(path))


def compare_reviews(baseline, candidate):
    original = json.loads((baseline / "summary.json").read_text())
    trained = json.loads((candidate / "summary.json").read_text())
    before = json.loads((baseline / "manifest.json").read_text())
    after = json.loads((candidate / "manifest.json").read_text())
    for key in ("sample_sources", "generation", "source_sha256", "rules_sha256", "config_sha256",
                "dataset_sha256"):
        if before[key] != after[key]:
            raise ValueError("paired review evaluations differ in " + key)
    originals = {row["id"]: row for row in
                 map(json.loads, (baseline / "predictions.jsonl").read_text().splitlines())}
    predictions = list(map(json.loads, (candidate / "predictions.jsonl").read_text().splitlines()))
    paired = {}
    for metric in ("terminal_verdict_correct", "strict_correct"):
        paired[metric] = {
            "wrong_to_right": sum(not originals[row["id"]][metric] and row[metric] for row in predictions),
            "right_to_wrong": sum(originals[row["id"]][metric] and not row[metric] for row in predictions)}
    return {"completed": original["completed"] and trained["completed"],
            "samples": trained["planned_samples"], "sft": original["all"], "grpo": trained["all"],
            "delta": {key: trained["all"][key] - original["all"][key] for key in METRICS},
            "paired": paired,
            "case_id": {case: {"sft": original["case_id"][case], "grpo": values}
                        for case, values in trained["case_id"].items()}}


def snapshot_sources(args, settings, attempt):
    files = set(ROOT.glob("src/guiqa/**/*.py")) | set(ROOT.glob("scripts/*.py"))
    files.update((args.config, ROOT / "configs/sft-split.yaml", ROOT / "configs/sources.yaml",
                  ROOT / "configs/agent-review-evidence-v4-selected.yaml",
                  ROOT / "configs/rl-review-reward-rules.yaml"))
    files.update(ROOT / settings[key] for key in ("model_config", "reward_rules") if key in settings)
    stem = "training-source" if attempt == 1 else f"training-source-resume-{attempt:03d}"
    with tarfile.open(args.ops / (stem + ".tar.gz"), "w:gz") as archive:
        for path in sorted(files):
            archive.add(path, arcname=str(path.relative_to(ROOT)))
    write_json(args.ops / (stem + ".json"),
               {str(path.relative_to(ROOT)): file_hash(path) for path in sorted(files)})


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    main()
