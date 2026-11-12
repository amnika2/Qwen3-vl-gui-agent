"""Register sixteen separate model E2E conditions without executing a model."""

import hashlib
import json
from pathlib import Path

import yaml

from guiqa.dataset26_runtime import write_json


def main():
    root = Path(__file__).resolve().parents[1]
    config_path = root / "configs/e2e-dataset26-v1.yaml"
    config = yaml.safe_load(config_path.read_text())
    source = root / "vendor/SWEeper-Bench/test.jsonl"
    cases = {row["case_id"]: row for row in map(json.loads, source.read_text().splitlines())}
    output = root / "runs/dataset26-v1/e2e"
    output.mkdir(exist_ok=True)
    original = yaml.safe_load(cases["sweeper-043"]["workflows"])["workflows"][0]["steps"][3]
    payload = original.split("not this export: ", 1)[1].encode()
    json.loads(payload)
    (output / "etherpad-legacy.etherpad").write_bytes(payload)
    rows = []
    for case_id, entry in config["cases"].items():
        case = cases[case_id]
        for build in config["builds"]:
            rows.append({"id": f"d26-e2e-{case_id}-{build}", "instance_id": f"d26-e2e-{case_id}",
                "case_id": case_id, "category": entry["category"], "build": build,
                "task": entry["task"].strip(), "source_commit": case["base_commit" if build == "A" else "reference_commit"],
                "setup_profile": "official_target_fixture",
                "start": "login_page" if entry["category"] == "A" else "prepared_business_page",
                "input_files": [entry["input_file"]] if "input_file" in entry else [],
                "evaluation_axis": "unseen_operation_category" if entry["category"] in {"A", "B"} else "unseen_application",
                "executed": False})
    (output / "conditions.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    write_json(output / "manifest.json", {"conditions": len(rows), "executed": False,
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "input_files": {"etherpad-legacy.etherpad": {"bytes": len(payload),
                       "sha256": hashlib.sha256(payload).hexdigest(), "source_case": "sweeper-043"}}})
    print(json.dumps({"conditions": len(rows), "directory": str(output), "input_bytes": len(payload)}))


if __name__ == "__main__":
    main()
