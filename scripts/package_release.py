import hashlib
import json
import re
import tarfile
from pathlib import Path

from guiqa.artifacts import source_digest


def main():
    root = Path(__file__).resolve().parents[1]
    files = {root / name for name in ("README.md", "pyproject.toml", "requirements.lock.txt", ".gitignore",
                                    ".github/workflows/smoke.yml", "Qwen3VL_GUI_Regression_Agent_Plan.md", "cases/README.md", "cases/index.csv")}
    patterns = ("src/guiqa/*.py", "scripts/*.py", "scripts/*.sh", "configs/*.yaml", "tests/*.py",
                "cases/*/public.json", "docs/*.md", "docs/*.json", "docs/*.freeze.txt",
                "reports/*.csv", "reports/*.md", "reports/*.xml", "reports/protocol*.tar.gz",
                "reports/*/summary.md", "reports/*/results.csv")
    for pattern in patterns:
        files.update(root.glob(pattern))
    files.discard(root / "docs/release-manifest.json")
    selected = set(re.findall(r"\.\./runs/([^/]+)/", (root / "reports/demo.md").read_text()))
    for run_id in selected:
        directory = root / "runs" / run_id
        for pattern in ("screenshots/*.png", "*.json", "*.jsonl", "report.md", "junit.xml", "private/oracle.json", "private/manifest.json"):
            files.update(directory.glob(pattern))
    archive = root / "reports/guiqa-source-and-demo.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for path in sorted(files):
            bundle.add(path, arcname=str(path.relative_to(root)))
    manifest = {"archive": str(archive.relative_to(root)), "bytes": archive.stat().st_size,
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "source_hash": source_digest(root),
                "files": len(files), "demo_runs": sorted(selected),
                "external_downloads": "Pinned model, dataset, application sources and dependencies are prepared by README commands",
                "formal_protocols": ["docs/protocol-freeze.json", "docs/protocol-extension-round1-freeze.json", "docs/protocol-extension-freeze.json"]}
    (root / "docs/release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
