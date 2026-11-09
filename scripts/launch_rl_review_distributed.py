"""Transfer preserved candidates unchanged and finish missing reviews on five GPUs."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile

from guiqa.rl_review_diagnostic import load_run, validate_saved


ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "reports/rl-review-diagnostic-v1"
OUTPUT = ROOT / "reports/rl-review-diagnostic-v1-distributed"
RUNTIME = ROOT / "runs/rl-review-pilot-v1-distributed"
ASSIGNMENTS = [(0, "402", 0), (1, "402", 1), (2, "401", 1), (3, "local", 2), (4, "local", 3)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("launch", "spawn", "worker"))
    parser.add_argument("--worker", type=int, choices=range(5))
    parser.add_argument("--gpu", type=int)
    args = parser.parse_args()
    if args.mode == "launch":
        transfer_candidates()
        launch_all()
    elif args.mode == "spawn":
        print(json.dumps(spawn_worker(args.worker, args.gpu)), flush=True)
    else:
        run_worker(args.worker)


def transfer_candidates():
    manifest, selected = load_run(ORIGINAL)
    source_paths = [ORIGINAL / f"shard-{index}.jsonl" for index in range(2)]
    source_hashes = {str(path): checksum(path) for path in source_paths}
    lines = []
    for index, path in enumerate(source_paths):
        original = path.read_bytes().splitlines(keepends=True)
        rows = [json.loads(line) for line in original]
        validate_saved(rows, selected, manifest, index, 2)
        if len(rows) != (184, 256)[index]:
            raise ValueError("source-ready counts must be shard0=184 and shard1=256")
        lines.extend(zip(rows, original))
    identities = [(row["id"], row["candidate_index"]) for row, _ in lines]
    if len(lines) != 440 or len(set(identities)) != 440:
        raise ValueError("the source must contain exactly 440 unique completed candidates")
    for row, line in lines:
        if not line.endswith(b"\n") or hashlib.sha256(row["raw"].encode()).hexdigest() != row["raw_sha256"]:
            raise ValueError("source record must be complete and match its raw-answer hash")

    OUTPUT.mkdir(exist_ok=False)
    RUNTIME.mkdir(exist_ok=False)
    for name in ("manifest.json", "selected.jsonl"):
        shutil.copyfile(ORIGINAL / name, OUTPUT / name)
    records = []
    for shard in range(5):
        assigned = [(row, line) for row, line in lines if row["sample_index"] % 5 == shard]
        validate_saved([row for row, _ in assigned], selected, manifest, shard, 5)
        destination = OUTPUT / f"shard-{shard}.jsonl"
        destination.write_bytes(b"".join(line for _, line in assigned))
        planned = sum(index % 5 == shard for index in range(len(selected))) * manifest["candidates_per_sample"]
        records.append({"shard": shard, "preserved": len(assigned), "planned": planned,
                        "remaining": planned - len(assigned), "initial_sha256": checksum(destination)})
    transferred = [line for index in range(5) for line in (OUTPUT / f"shard-{index}.jsonl").read_bytes().splitlines(keepends=True)]
    if sorted(transferred) != sorted(line for _, line in lines):
        raise ValueError("candidate transfer changed, duplicated or dropped an original JSON line")
    if any(checksum(Path(name)) != digest for name, digest in source_hashes.items()):
        raise ValueError("source candidates changed during the transfer")
    load_run(OUTPUT)
    proof = {"source": str(ORIGINAL), "destination": str(OUTPUT), "original_candidates": 440,
             "remaining_candidates": sum(row["remaining"] for row in records), "shards": records,
             "original_shard_sha256": source_hashes,
             "manifest_sha256": checksum(OUTPUT / "manifest.json"),
             "selected_sha256": checksum(OUTPUT / "selected.jsonl"),
             "manifest_and_selection_byte_identical": all((ORIGINAL / name).read_bytes() == (OUTPUT / name).read_bytes()
                                                           for name in ("manifest.json", "selected.jsonl")),
             "all_440_unique_sources_match": True, "all_440_json_lines_byte_identical": True,
             "frozen_source_hashes_verified": manifest["source_files"], "only_missing_candidates_generate": True}
    write_json(RUNTIME / "transfer-proof.json", proof)
    snapshot_sources(manifest)
    print(json.dumps({"transferred": 440, "remaining": proof["remaining_candidates"], "shards": records}), flush=True)


def launch_all():
    workers = []
    for shard, host, gpu in ASSIGNMENTS:
        command = [str(ROOT / ".model-venv/bin/python"), "-u", str(Path(__file__).resolve()),
                   "spawn", "--worker", str(shard), "--gpu", str(gpu)]
        if host == "local":
            record = spawn_worker(shard, gpu)
        else:
            remote = ["ssh", "-o", "BatchMode=yes", "-o", "ClearAllForwardings=yes", host, shlex.join(command)]
            record = json.loads(subprocess.check_output(remote, text=True, cwd=ROOT))
        record.update(host_alias=host, spawn_command=command)
        workers.append(record)
        write_json(RUNTIME / "launch.json", {"output": str(OUTPUT), "shards": 5,
                   "manifest_sha256": checksum(OUTPUT / "manifest.json"), "workers": workers})
        print(json.dumps(record), flush=True)


def spawn_worker(index, gpu):
    memory = subprocess.check_output(["nvidia-smi", f"--id={gpu}", "--query-gpu=memory.used",
                                      "--format=csv,noheader,nounits"], text=True).strip()
    if int(memory) > 100:
        raise RuntimeError(f"GPU {gpu} is occupied: {memory} MiB")
    command = [str(ROOT / ".model-venv/bin/python"), "-u", str(Path(__file__).resolve()), "worker", "--worker", str(index)]
    environment = dict(os.environ, CUDA_VISIBLE_DEVICES=str(gpu), OMP_NUM_THREADS="4",
                       TOKENIZERS_PARALLELISM="false", PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True")
    with (RUNTIME / f"worker-{index}.log").open("w") as log:
        process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, start_new_session=True)
    return {"shard": index, "host": socket.gethostname(), "gpu": gpu, "pid": process.pid,
            "command": command, "log": str(RUNTIME / f"worker-{index}.log"),
            "environment": {key: environment[key] for key in ("CUDA_VISIBLE_DEVICES", "OMP_NUM_THREADS",
                            "TOKENIZERS_PARALLELISM", "PYTORCH_CUDA_ALLOC_CONF")}, "gpu_memory_before_mib": int(memory)}


def run_worker(index):
    load_run(OUTPUT)
    command = [sys.executable, "-u", str(ROOT / "scripts/diagnose_rl_reviews_fast.py"), "worker",
               "--output", str(OUTPUT), "--shard-index", str(index), "--shards", "5"]
    process = subprocess.Popen(command, cwd=ROOT)
    write_json(RUNTIME / f"worker-{index}-start.json", {"host": socket.gethostname(), "pid": os.getpid(),
               "worker_pid": process.pid, "command": command, "gpu": os.environ["CUDA_VISIBLE_DEVICES"]})
    code = process.wait()
    write_json(RUNTIME / f"worker-{index}-exit.json", {"exit_code": code, "host": socket.gethostname(),
               "pid": os.getpid(), "worker_pid": process.pid})
    raise SystemExit(code)


def snapshot_sources(manifest):
    for name in ("diagnostic-source.tar.gz", "diagnostic-source.json"):
        shutil.copyfile(ROOT / "runs/rl-review-pilot-v1" / name, RUNTIME / ("original-" + name))
    frozen = [Path(name) for name in manifest["source_files"] if name.endswith((".py", ".yaml"))]
    added = [ROOT / name for name in ("src/guiqa/lmfe_traversal.py", "scripts/diagnose_rl_reviews_fast.py",
                                     "scripts/launch_rl_review_distributed.py")]
    with tarfile.open(RUNTIME / "distributed-source.tar.gz", "w:gz") as archive:
        for path in frozen + added:
            archive.add(path, arcname=str(path.relative_to(ROOT)))
    write_json(RUNTIME / "distributed-source.json", {str(path.relative_to(ROOT)): checksum(path) for path in frozen + added})


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
