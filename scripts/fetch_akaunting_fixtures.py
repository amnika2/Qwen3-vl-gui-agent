import hashlib
import io
import json
import tarfile
from pathlib import Path

import requests


def main():
    root = Path(__file__).resolve().parents[1]
    metadata = json.loads((root / "docs/akaunting-fixture-manifest.json").read_text())
    repository = "evigbyen/sweeper-005"
    session = requests.Session()
    response = session.get("https://ghcr.io/token", params={"service": "ghcr.io", "scope": f"repository:{repository}:pull"}, timeout=30)
    response.raise_for_status()
    session.headers["Authorization"] = "Bearer " + response.json()["token"]
    layers = {
        "akaunting-router.php": "sha256:41cbdba94c2c53b5e055102a00ba4e862d10a021d5f4be59909013e5df895bb9",
        "akaunting-seed-caches.php": "sha256:066effcfdf399781b9515f2c113c972452453e03a0aeaeb80077eb50d3f42bf4",
    }
    destination = root / "vendor/akaunting-image/helpers/opt"
    destination.mkdir(parents=True, exist_ok=True)
    for name, checksum in layers.items():
        response = session.get(f"https://ghcr.io/v2/{repository}/blobs/{checksum}", timeout=60)
        response.raise_for_status()
        if hashlib.sha256(response.content).hexdigest() != checksum.split(":")[1]:
            raise ValueError("OCI layer checksum mismatch")
        with tarfile.open(fileobj=io.BytesIO(response.content)) as archive:
            content = archive.extractfile("opt/" + name).read()
        if hashlib.sha256(content).hexdigest() != metadata["fixtures"][name]:
            raise ValueError("upstream fixture checksum mismatch")
        (destination / name).write_bytes(content)
        print("Verified upstream fixture:", name)


if __name__ == "__main__":
    main()
