import hashlib
import json
from pathlib import Path


root = Path(__file__).resolve().parents[1]
run = root / "runs/dataset26-v1"
locations = {
    "sweeper-125": run / "calibration",
    "sweeper-142": run / "calibration",
    "sweeper-195": run / "existing-wolf-calibration/calibration",
    "sweeper-196": run / "existing-wonder-calibration/calibration",
    "sweeper-034": run / "existing-doku-calibration/calibration",
    "sweeper-053": run / "flatpress-cache-calibration/calibration",
    "sweeper-005": run / "calibration",
}
records = []
for case, directory in locations.items():
    for build, expected in (("A", "fail"), ("B", "pass")):
        path = directory / case / f"d26-{case}-101-{build}"
        result = json.loads((path / "result.json").read_text())
        assert result["complete"] and result["verdict"] == expected
        assert result["operation_samples"] == 5 and result["review_samples"] == 1
        for name, digest in result["sha256"].items():
            assert hashlib.sha256((path / name).read_bytes()).hexdigest() == digest
        records.append({"case_id": case, "build": build, "expected_verdict": expected,
                        "observed_verdict": result["verdict"], "directory": str(path.relative_to(root)),
                        "source_commit": result["source_commit"], "action_count": result["action_count"],
                        "result_sha256": hashlib.sha256((path / "result.json").read_bytes()).hexdigest()})
value = {"complete": True, "calibration_only": True, "applications": 7, "trajectories": 14,
         "operation_samples": 70, "review_samples": 14, "counted_in_dataset_quota": False,
         "calibration": records}
destination = run / "verification-existing-calibration.json"
destination.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({"verified": True, "applications": 7, "trajectories": 14, "path": str(destination)}))
