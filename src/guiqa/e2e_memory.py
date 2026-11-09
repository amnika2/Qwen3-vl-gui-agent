"""Build a frozen procedural memory from identified training trajectories."""

import hashlib
import json
from pathlib import Path

import yaml

from .agent_memory import ExperienceStore


def build_memory(root, seed_file, output):
    root, seed_file, output = Path(root), Path(root) / seed_file, Path(root) / output
    if output.exists():
        raise FileExistsError(output)
    experiences = read_seed(root, seed_file)
    store = ExperienceStore(output)
    records = []
    for experience in experiences:
        saved = store.save("e2e-frozen", **{key: experience[key] for key in (
            "case_id", "workflow", "task", "procedure", "lessons", "source_run_id")})
        records.append({**saved, "case_id": experience["case_id"], "workflow": experience["workflow"],
                        "sources": experience["sources"]})
    return {"database": str(output), "namespace": "e2e-frozen", "count": len(records),
            "seed_file": str(seed_file), "seed_sha256": file_hash(seed_file),
            "database_sha256": file_hash(output), "experiences": records}


def read_seed(root, seed_file):
    experiences = yaml.safe_load(seed_file.read_text())["experiences"]
    split = yaml.safe_load((root / "configs/sft-split.yaml").read_text())
    training_cases = set(split["training_pilot"] + split["training_supplementary"])
    if not experiences:
        raise ValueError("the frozen memory seed must contain training experiences")
    for experience in experiences:
        if experience["case_id"] not in training_cases:
            raise ValueError(f"memory source is outside the training applications: {experience['case_id']}")
        for name, expected_hash in experience["sources"].items():
            if file_hash(root / name) != expected_hash:
                raise ValueError(f"memory source hash differs: {name}")
        source_path = root / "runs" / experience["source_run_id"] / "result.json"
        source = json.loads(source_path.read_text())
        if any(experience[key] != source[key] for key in ("case_id", "workflow", "task")):
            raise ValueError(f"memory source identity or public task differs: {source_path}")
    return experiences


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
