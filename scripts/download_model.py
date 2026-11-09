import hashlib
import json
from pathlib import Path

import yaml
import requests
from huggingface_hub import snapshot_download


def main():
    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / "configs/sources.yaml").read_text())["model"]
    location = root / "vendor/models/Qwen3-VL-8B-Instruct"
    snapshot_download(config["repo"], revision=config["revision"], local_dir=location, max_workers=4)
    files = {}
    for file in sorted(location.glob("*")):
        if file.is_file():
            checksum = hashlib.sha256()
            with file.open("rb") as handle:
                while chunk := handle.read(8 * 1024 * 1024):
                    checksum.update(chunk)
            files[file.name] = {"size": file.stat().st_size, "sha256": checksum.hexdigest()}
    (root / "docs/model-manifest.json").write_text(json.dumps({**config, "files": files}, indent=2) + "\n")
    response = requests.get(f"https://huggingface.co/api/models/{config['repo']}/tree/{config['revision']}", timeout=30)
    response.raise_for_status()
    checks = []
    for entry in response.json():
        if entry["path"].endswith(".safetensors"):
            actual = files[entry["path"]]
            checks.append({"file": entry["path"], "sha256_matches": actual["sha256"] == entry["lfs"]["oid"],
                           "size_matches": actual["size"] == entry["size"]})
    passed = bool(checks) and all(item["sha256_matches"] and item["size_matches"] for item in checks)
    (root / "docs/model-verification.json").write_text(json.dumps({"revision": config["revision"], "passed": passed, "checks": checks}, indent=2) + "\n")
    if not passed:
        raise ValueError("model weights differ from pinned upstream LFS objects")


if __name__ == "__main__":
    main()
