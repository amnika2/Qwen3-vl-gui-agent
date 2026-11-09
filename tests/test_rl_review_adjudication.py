import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from adjudicate_rl_review_diagnostic import apply_adjudication


@pytest.mark.parametrize("candidate_index,invalid_paths", [
    (0, {"field_readings.Title.before.step"}),
    (7, {"field_readings.Title.before.step", "field_readings.Slug.before.step"}),
])
def test_real_wolf_aliases_do_not_turn_reopened_values_into_preservation_baselines(
        candidate_index, invalid_paths):
    report = Path(__file__).resolve().parents[1] / "reports/rl-review-diagnostic-v1"
    adjudications = json.loads((report / "adjudications.json").read_text())
    source = json.loads((report / "selected.jsonl").read_text().splitlines()[0])
    candidates = [json.loads(line) for line in (report / "shard-0.jsonl").read_text().splitlines()]
    prediction = next(row for row in candidates
                      if row["id"] == source["id"] and row["candidate_index"] == candidate_index)
    raw, raw_hash = prediction["raw"], prediction["raw_sha256"]

    result = apply_adjudication(prediction, source, adjudications["samples"][source["id"]])

    assert result["verdict_correct"]
    assert not result["strict_correct"]
    assert not result["core_facts_correct"]
    assert {item["path"] for item in result["adjudicated_invalid_citations"]} == invalid_paths
    assert not result["unverified_fields"]["extra"]
    assert not any("requested_value:mismatch" in error for error in result["errors"])
    assert result["reward"] < 0
    assert result["raw"] == prediction["raw"] == raw
    assert result["raw_sha256"] == prediction["raw_sha256"] == raw_hash
    assert hashlib.sha256(raw.encode()).hexdigest() == raw_hash
