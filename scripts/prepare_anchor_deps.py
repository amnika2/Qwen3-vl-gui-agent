import hashlib
import json
import shutil
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    original = root / "vendor/apps/anchor-cms/base"
    destination = root / "vendor/anchor-deps"
    destination.mkdir(exist_ok=True)
    manifest = json.loads((original / "composer.json").read_text())
    manifest["type"] = "project"
    (destination / "composer.json").write_text(json.dumps(manifest, indent=2) + "\n")
    shutil.copy2(original / "composer.lock", destination / "composer.lock")
    provenance = {"change": "Normalize Composer package type CMS to project in a separate dependency directory",
                  "source_modified": False,
                  "original_manifest_sha256": hashlib.sha256((original / "composer.json").read_bytes()).hexdigest(),
                  "normalized_manifest_sha256": hashlib.sha256((destination / "composer.json").read_bytes()).hexdigest(),
                  "lock_sha256": hashlib.sha256((original / "composer.lock").read_bytes()).hexdigest()}
    (root / "docs/anchor-dependency-manifest.json").write_text(json.dumps(provenance, indent=2) + "\n")


if __name__ == "__main__":
    main()
