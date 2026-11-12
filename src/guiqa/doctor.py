import importlib
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from .artifacts import write_json
from .browser import browser_smoke


def doctor(root: Path, config=None):
    result = {"hostname": socket.gethostname(), "platform": platform.platform(), "python": sys.version, "executable": sys.executable,
              "disk_free_gib": shutil.disk_usage(root).free / 1024 ** 3,
              "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"), "checks": {}}
    commands = {"gpu": ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.free", "--format=csv"],
                "memory": ["free", "-h"], "git": ["git", "--version"],
                "docker": ["docker", "info", "--format", "{{.ServerVersion}}"],
                "native_php": [str(root / "vendor/runtime/php"), "-v"]}
    for name, command in commands.items():
        if not shutil.which(command[0]):
            result["checks"][name] = {"ok": False, "error": "executable missing"}
            continue
        result["checks"][name] = command_check(command)
    try:
        result["checks"]["browser"] = {"ok": True, **browser_smoke(root / "docs")}
    except Exception as error:
        result["checks"]["browser"] = {"ok": False, "error": str(error)}
    result["checks"]["cuda"] = cuda_check()
    required = ["browser", "native_php"]
    if config is not None:
        result["checks"]["model_files"] = model_files_check(root / config["model_path"])
        result["checks"]["model_dependencies"] = model_dependencies_check(config)
        required.extend(["model_files", "model_dependencies", "cuda"])
        if config.get("dataset") == "datasets/dataset26-v1":
            result["checks"]["dataset26"] = dataset26_check(root / config["dataset"])
            required.append("dataset26")
        if config.get("adapter_path"):
            result["checks"]["adapter_files"] = files_check(root / config["adapter_path"],
                ["adapter_config.json", "adapter_model.safetensors"])
            required.append("adapter_files")
        if config.get("agent_runtime", {}).get("enabled"):
            result["checks"]["mcp_dependencies"] = imports_check([
                "guiqa.agent_tools", "guiqa.agent_session", "guiqa.mcp_server"])
            result["checks"]["mcp_configuration"] = mcp_configuration_check(config["agent_runtime"])
            required.extend(["mcp_dependencies", "mcp_configuration"])
    result["required_checks"] = required
    result["ready"] = all(result["checks"][name]["ok"] for name in required)
    write_json(root / "docs" / ("doctor-model.json" if result["checks"]["cuda"]["ok"] else "doctor.json"), result)
    return result


def command_check(command):
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"ok": False, "error": f"{type(error).__name__}: {error}"}
    return {"ok": completed.returncode == 0,
            "output": completed.stdout.strip(), "error": completed.stderr.strip()}


def cuda_check():
    if not os.environ.get("CUDA_VISIBLE_DEVICES"):
        return {"ok": False, "error": "set CUDA_VISIBLE_DEVICES to an allocated GPU"}
    try:
        import torch
        value = torch.ones(1, device="cuda:0").sum().item()
        return {"ok": value == 1, "torch": torch.__version__,
                "device": torch.cuda.get_device_name(0)}
    except (ImportError, RuntimeError) as error:
        return {"ok": False, "error": f"{type(error).__name__}: {error}"}


def files_check(directory, names):
    missing = [str(directory / name) for name in names if not (directory / name).is_file()]
    return {"ok": not missing, "directory": str(directory), "missing": missing}


def model_files_check(directory):
    names = ["config.json", "tokenizer_config.json", "tokenizer.json", "preprocessor_config.json"]
    index = directory / "model.safetensors.index.json"
    if index.is_file():
        try:
            names.extend(sorted(set(json.loads(index.read_text())["weight_map"].values())))
        except (OSError, ValueError, KeyError) as error:
            return {"ok": False, "directory": str(directory), "error": f"{type(error).__name__}: {error}"}
    else:
        names.append("model.safetensors")
    return files_check(directory, names)


def imports_check(names):
    errors = {}
    for name in names:
        try:
            importlib.import_module(name)
        except (ImportError, RuntimeError, OSError) as error:
            errors[name] = f"{type(error).__name__}: {error}"
    return {"ok": not errors, "modules": names, "errors": errors}


def model_dependencies_check(config):
    names = ["torch", "torchvision", "transformers.models.qwen3_vl.modeling_qwen3_vl",
             "transformers.models.qwen3_vl.processing_qwen3_vl", "accelerate"]
    if config.get("adapter_path"):
        names.append("peft")
    if config.get("agent_runtime", {}).get("enabled") or config.get("review_format") in {
        "evidence_v2", "evidence_facts_v1", "business_evidence_v1"
    }:
        names.append("lmformatenforcer.integrations.transformers")
    return imports_check(names)


def dataset26_check(directory):
    files = files_check(directory, ["plan.json", "manifest.json"])
    if not files["ok"]:
        return files
    manifest = json.loads((directory / "manifest.json").read_text())
    plan = json.loads((directory / "plan.json").read_text())
    execution = imports_check(["guiqa.dataset26_execution", "guiqa.dataset26_oracle", "guiqa.business_review"])
    plan_matches = hashlib.sha256((directory / "plan.json").read_bytes()).hexdigest() == manifest["plan_sha256"]
    applications = plan["applications"]
    metadata_ready = (manifest["dataset"] == "dataset26-v1" and manifest["complete"]
                      and manifest["review_format"] == "business_evidence_v1"
                      and len(applications) == len({app["repo"] for app in applications}) == 26)
    return {"ok": metadata_ready and plan_matches and execution["ok"], "directory": str(directory),
            "applications": len(applications), "metadata_ready": metadata_ready,
            "plan_matches_manifest": plan_matches, "execution_modules": execution,
            "scope": "package metadata and execution imports; applications are not started"}


def mcp_configuration_check(settings):
    errors = {}
    for server in settings.get("mcp_servers", []):
        if "command" in server:
            if not shutil.which(server["command"]):
                errors[server["name"]] = f"executable missing: {server['command']}"
        else:
            url = urlparse(server["url"])
            if url.scheme not in {"http", "https"} or not url.netloc:
                errors[server["name"]] = "MCP URL must use http or https and include a host"
    return {"ok": not errors, "memory_database": settings["memory_database"],
            "memory_namespace": settings["memory_namespace"], "errors": errors}
