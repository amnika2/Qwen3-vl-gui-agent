import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from .artifacts import write_json
from .browser import browser_smoke


def doctor(root: Path):
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
        completed = subprocess.run(command, capture_output=True, text=True, timeout=20)
        result["checks"][name] = {"ok": completed.returncode == 0,
            "output": completed.stdout.strip(), "error": completed.stderr.strip()}
    try:
        result["checks"]["browser"] = {"ok": True, **browser_smoke(root / "docs")}
    except Exception as error:
        result["checks"]["browser"] = {"ok": False, "error": str(error)}
    try:
        import torch
        value = torch.ones(1, device="cuda:0").sum().item()
        result["checks"]["cuda"] = {"ok": value == 1, "torch": torch.__version__,
                       "device": torch.cuda.get_device_name(0)}
    except (ImportError, RuntimeError) as error:
        result["checks"]["cuda"] = {"ok": False, "error": str(error)}
    write_json(root / "docs" / ("doctor-model.json" if result["checks"]["cuda"]["ok"] else "doctor.json"), result)
    return result
