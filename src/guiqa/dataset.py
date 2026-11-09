import csv
import hashlib
import json
import re
from pathlib import Path

import requests
import yaml

DATASET = "zlab-princeton/SWEeper-Bench"
CANDIDATES = ["sweeper-005", "sweeper-040", "sweeper-008", "sweeper-007", "sweeper-041"]


def prepare(root: Path, revision: str):
    from .artifacts import write_json

    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("dataset revision must be a full commit SHA")
    url = f"https://huggingface.co/datasets/{DATASET}/resolve/{revision}/data/test.jsonl"
    response = requests.get(url, timeout=90)
    response.raise_for_status()
    rows = parse_dataset(response.content)
    original = root / "vendor/SWEeper-Bench/test.jsonl"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(response.content)
    manifest = {"repo": DATASET, "revision": revision, "source_url": url,
                "sha256": hashlib.sha256(response.content).hexdigest(), "rows": len(rows),
                "license": "CC-BY-4.0", "authors": "SWEeper-Bench authors (Z Lab, Princeton)",
                "modifications": "Original JSONL bytes preserved; candidate metadata extracted separately."}
    write_json(root / "docs/data-manifest.json", manifest)
    selected = {row["case_id"]: row for row in rows if row["case_id"] in CANDIDATES}
    with (root / "cases/index.csv").open("w") as file:
        columns = ["case_id", "slug", "repo", "base_commit", "reference_commit", "status"]
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        for case_id in CANDIDATES:
            row = selected[case_id]
            write_json(root / "cases" / case_id / "private.json", row)
            writer.writerow({**{key: row[key] for key in columns[:-1]}, "status": "candidate"})
    return manifest


def parse_dataset(raw: bytes):
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    seen = set()
    for row in rows:
        for key in ("case_id", "slug", "repo", "base_commit", "reference_commit", "task_scoped", "task_specified", "workflows"):
            if not isinstance(row.get(key), str) or not row[key]:
                raise ValueError(f"missing or invalid field: {key}")
        if row["case_id"] in seen:
            raise ValueError("duplicate case_id")
        seen.add(row["case_id"])
        for key in ("base_commit", "reference_commit"):
            if not re.fullmatch(r"[0-9a-f]{40}", row[key]):
                raise ValueError(f"invalid {key}")
        flows = yaml.safe_load(row["workflows"])["workflows"]
        if sorted(flow["category"] for flow in flows) != ["preservation", "target"]:
            raise ValueError(f"{row['case_id']}: expected target and preservation")
        if any(not isinstance(flow.get("steps"), list) or not flow["steps"] for flow in flows):
            raise ValueError("workflow steps must be nonempty lists")
    return rows
