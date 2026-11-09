import ast
import hashlib
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path

from guiqa.artifacts import source_digest


def main():
    root = Path(__file__).resolve().parents[1]
    checks = {}
    with tarfile.open(root / "reports/protocol-source.tar.gz") as previous:
        for name in ("src/guiqa/model.py", "src/guiqa/ledger.py", "src/guiqa/actions.py", "src/guiqa/browser.py",
                     "configs/agent.yaml", "cases/sweeper-005/public.json", "cases/sweeper-040/public.json", "cases/sweeper-041/public.json"):
            checks[name] = previous.extractfile(name).read() == (root / name).read_bytes()
        checks["runner.agent"] = agent_source(previous.extractfile("src/guiqa/runner.py").read().decode()) == agent_source((root / "src/guiqa/runner.py").read_text())
    if not all(checks.values()):
        raise RuntimeError(f"Frozen controller changed: {checks}")
    archive = root / "reports/protocol-extension-source.tar.gz"
    files = [root / name for name in ("README.md", "pyproject.toml", "requirements.lock.txt", ".github/workflows/smoke.yml")]
    for pattern in ("src/guiqa/*.py", "scripts/*.py", "scripts/*.sh", "configs/*.yaml", "tests/*.py", "cases/*/public.json"):
        files.extend(root.glob(pattern))
    with tarfile.open(archive, "w:gz") as bundle:
        for path in sorted(files):
            bundle.add(path, arcname=str(path.relative_to(root)))
    protocol = {"frozen_at": datetime.now(timezone.utc).isoformat(), "source_hash": source_digest(root),
                "source_archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "unchanged_from_round3": checks, "development": ["sweeper-041", "sweeper-005"],
                "additional_holdout": ["sweeper-007", "sweeper-008"], "existing_holdout": ["sweeper-040"],
                "selection": "Original predeclared candidates, admitted only after independent script calibration",
                "methods": ["script", "qwen-base", "qwen-improved"], "repeats": 2,
                "additional_planned_runs": 48, "total_planned_runs": 120,
                "gpu_host": "server402", "gpu_ids": [0, 1],
                "budgets": {"browser_actions": 30, "wall_seconds_excluding_model_load": 600},
                "prior_protocol": "docs/protocol-freeze.json"}
    (root / "docs/protocol-extension-freeze.json").write_text(json.dumps(protocol, indent=2) + "\n")
    print(json.dumps(protocol, indent=2))


def agent_source(source):
    function = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == "agent")
    return ast.get_source_segment(source, function)


if __name__ == "__main__":
    main()
