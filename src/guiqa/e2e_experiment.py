"""Frozen paired GUI experiments, with one worker owning each group."""

from copy import deepcopy
import gc
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

import yaml

from .artifacts import source_digest, write_json
from .cli import load_config, load_model
from .runner import load_case, run_case


def prepare(root, suite_path, directory):
    from .e2e_memory import build_memory

    suite = yaml.safe_load(suite_path.read_text())
    groups = []
    for item in suite["groups"]:
        overrides = item.get("overrides", {})
        if set(overrides) - {"planning", "memory", "reflection"} or any(type(value) is not bool for value in overrides.values()):
            raise ValueError("group overrides must be boolean planning, memory or reflection flags")
        config = load_config(root / item["config"])
        settings = config["agent_runtime"]
        settings.update(planning=True, memory=True, reflection=True)
        settings.update(overrides)
        groups.append({"id": item["id"], "config": config,
                       "adapter_sha256": file_hash(Path(config["adapter_path"]) / "adapter_model.safetensors")
                       if config.get("adapter_path") else None})
    inputs = {}
    for case in suite["cases"]:
        load_case(root, case, suite["workflow"])
        for name in ("public.json", "fixtures.private.json"):
            path = root / "cases" / case / name
            inputs[str(path.relative_to(root))] = file_hash(path)
    directory.mkdir(parents=True, exist_ok=False)
    seed = build_memory(root, root / suite["memory_seed"], directory / "memory-seed.sqlite3")
    cells = [{"id": f"{group['id']}-{case}-{build}", "group": group["id"], "case_id": case,
              "build": build, "workflow": suite["workflow"], "repeat": 0}
             for group in groups for case in suite["cases"] for build in suite["builds"]]
    protocol = {"name": suite["name"], "suite": suite, "groups": groups, "cells": cells,
                "memory_seed": seed, "memory_sha256": file_hash(directory / "memory-seed.sqlite3"),
                "source_hash": source_digest(root), "task_hashes": inputs,
                "policy": "first saved attempt; no online memory transfer between evaluation tasks"}
    write_json(directory / "protocol.json", protocol)
    files = [*root.glob("src/guiqa/**/*.py"), root / "scripts/run_e2e_experiment.py",
             root / "scripts/build_e2e_memory.py", root / "pyproject.toml", suite_path,
             root / suite["memory_seed"], *[root / group["config"] for group in suite["groups"]]]
    with tarfile.open(directory / "source.tar.gz", "w:gz") as archive:
        for path in sorted(set(files)):
            archive.add(path, arcname=str(path.relative_to(root)))
    return protocol


def run_worker(root, directory, worker, workers, port):
    protocol = json.loads((directory / "protocol.json").read_text())
    model, model_key = None, None
    for group_index, group in enumerate(protocol["groups"]):
        if group_index % workers != worker:
            continue
        pending = [cell for cell in protocol["cells"] if cell["group"] == group["id"]
                   and not (directory / "cells" / (cell["id"] + ".json")).exists()]
        if not pending:
            continue
        config = group["config"]
        key = (config["model_path"], config.get("adapter_path"))
        if key != model_key:
            if model is not None:
                del model
                gc.collect()
                import torch
                torch.cuda.empty_cache()
            model = load_model("qwen-controlled", config)
            model.load()
            model_key = key
        for cell in pending:
            cell_config = deepcopy(config)
            database = directory / "memory" / (cell["id"] + ".sqlite3")
            cell_config["agent_runtime"].update(memory_database=str(database), memory_namespace="e2e-frozen")
            if cell_config["agent_runtime"]["memory"]:
                database.parent.mkdir(exist_ok=True)
                shutil.copy2(directory / "memory-seed.sqlite3", database)
            model.config = cell_config
            model.begin_run()

            def started(run_directory):
                write_json(directory / f"worker-{worker}.json", {
                    "status": "running", "cell": cell, "run_dir": str(run_directory)})

            run_directory, result = run_case(
                root, cell["case_id"], cell["build"], cell["workflow"], "qwen-controlled", cell_config,
                model=model, port=port, experiment=directory.name + "/" + group["id"], on_started=started)
            record = {**cell, "run_dir": str(run_directory), "result": result}
            write_json(directory / "cells" / (cell["id"] + ".json"), record)
            print(json.dumps({"cell": cell["id"], "agent": result["agent_verdict"],
                              "oracle": result["oracle_verdict"], "actions": result["actions"],
                              "seconds": result["elapsed_seconds"], "run_dir": str(run_directory)},
                             ensure_ascii=False), flush=True)
            if result["termination_reason"] == "interrupted":
                write_json(directory / f"worker-{worker}.json", {"status": "interrupted", "cell": cell})
                return 130
    write_json(directory / f"worker-{worker}.json", {"status": "completed"})
    return 0


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
