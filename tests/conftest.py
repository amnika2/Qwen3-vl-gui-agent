import hashlib
import json
from pathlib import Path

import pytest
import yaml

from guiqa.artifacts import write_json


@pytest.fixture
def dataset26_package(tmp_path):
    root = tmp_path / "dataset26-root"
    dataset = root / "datasets/dataset26-v1"
    config = yaml.safe_load((Path(__file__).resolve().parents[1] / "configs/dataset26-v1.yaml").read_text())
    applications = [{"case_id": case_id, "split": split, "category": category,
                     "repo": "https://example.test/" + case_id,
                     "base_commit": "1" * 40, "reference_commit": "2" * 40}
                    for split, categories in config["applications"].items()
                    for category, cases in categories.items() for case_id in cases]
    write_json(dataset / "plan.json", {"applications": applications})
    conditions = [{"id": f"d26-e2e-{app['case_id']}-{build}",
                   "instance_id": "d26-e2e-" + app["case_id"],
                   "case_id": app["case_id"], "category": app["category"], "build": build,
                   "source_commit": app["base_commit" if build == "A" else "reference_commit"],
                   "task": "Synthetic public task for " + app["case_id"],
                   "evaluation_axis": "unseen_operation_category" if app["category"] in {"A", "B"}
                                      else "unseen_application"}
                  for app in applications if app["split"] == "test" for build in ("A", "B")]
    conditions_path = dataset / "e2e/conditions.jsonl"
    conditions_path.parent.mkdir()
    conditions_path.write_text("".join(json.dumps(row) + "\n" for row in conditions))
    write_json(dataset / "manifest.json", {
        "dataset": "dataset26-v1", "complete": True, "review_format": "business_evidence_v1",
        "plan_sha256": hashlib.sha256((dataset / "plan.json").read_bytes()).hexdigest(),
        "e2e_conditions_sha256": hashlib.sha256(conditions_path.read_bytes()).hexdigest()})
    return root
