import copy
import json
from collections import Counter
from pathlib import Path

import pytest
import yaml

from guiqa.review_facts import parse_facts, to_facts
from guiqa.sft_review_v5 import (build_dataset, select_operation_replay,
                                validate_partitions, verified_training_audit)
from guiqa.sft_v2 import checksum, read_json, read_rows, write_json


ROOT = Path(__file__).resolve().parents[1]
REVIEWS = ROOT / "datasets/rl-review-pilot-v2"
OPERATIONS = ROOT / "datasets/sft-main-10apps-v4"
PIXEL_AUDIT = ROOT / "docs/sft-review-v5-pixel-audit.json"


def test_real_facts_batch_keeps_development_gold_and_replays_original_operations(tmp_path):
    artifacts = [directory / name for directory in (REVIEWS, OPERATIONS)
                 for name in ("manifest.json", "train.jsonl", "validation.jsonl", "media_manifest.json")]
    hashes = {path: checksum(path) for path in artifacts}
    output = tmp_path / "v5"
    manifest, verification = build_dataset(ROOT, REVIEWS, OPERATIONS, PIXEL_AUDIT, output)
    assert verification["passed"]
    assert (manifest["train_samples"], manifest["validation_samples"]) == (290, 39)
    assert manifest["review_labels_by_split"]["train"] == {"pass": 75, "fail": 33, "blocked": 37}
    originals = {row["id"]: row for part in ("train", "validation")
                 for row in read_rows(REVIEWS / f"{part}.jsonl")}
    operation_originals = {row["id"]: row for row in read_rows(OPERATIONS / "train.jsonl")}
    for partition in ("train", "validation"):
        for row in read_rows(output / f"{partition}.jsonl"):
            if row["phase"] == "operation":
                assert row == operation_originals[row["id"]]
                continue
            original = originals[row["id"]]
            assert row["messages"][1:-1] == original["messages"][1:-1]
            assert row["data_split"] == original["data_split"]
            facts = json.loads(row["messages"][-1]["content"])
            assert facts == to_facts(json.loads(original["messages"][-1]["content"]))
            parsed = parse_facts(json.dumps(facts), {f["step"] for f in row["review_frames"]}, row["review_profile"])
            assert to_facts(parsed) == facts
    assert {path: checksum(path) for path in artifacts} == hashes
    assert manifest["agent_reviewed_new_training_samples"] == 40
    assert not verification["human_pixel_review_claimed"]
    for relative, record in read_json(output / "media_manifest.json").items():
        assert checksum(output / relative) == record["sha256"]


def test_replay_selection_is_deterministic_balanced_and_training_only():
    rows = read_rows(OPERATIONS / "train.jsonl") + read_rows(OPERATIONS / "validation.jsonl")
    selected = select_operation_replay(rows, 145)
    assert [row["id"] for row in selected] == [row["id"] for row in select_operation_replay(rows, 145)]
    assert len({row["id"] for row in selected}) == 145
    assert all(row["phase"] == "operation" and row["data_split"] == "train" for row in selected)
    counts = Counter(row["case_id"] for row in selected)
    assert len(counts) == 10
    assert max(counts.values()) - min(counts.values()) == 1


def test_replay_from_a_development_scenario_is_rejected():
    rows = read_rows(REVIEWS / "validation.jsonl")
    replay = copy.deepcopy(read_rows(OPERATIONS / "train.jsonl")[0])
    replay["scenario_fingerprint"] = rows[0]["scenario_fingerprint"]
    split = yaml.safe_load((ROOT / "configs/sft-split.yaml").read_text())
    with pytest.raises(ValueError, match="overlap on scenario_fingerprint"):
        validate_partitions(rows + [replay], split)


def test_pending_training_review_needs_all_its_gold_key_frames(tmp_path):
    audit = read_json(PIXEL_AUDIT)
    next(iter(audit["reviews"].values()))["reviewed_steps"] = []
    path = tmp_path / "audit.json"
    write_json(path, audit)
    with pytest.raises(ValueError, match="missing a gold evidence frame"):
        verified_training_audit(path, read_rows(REVIEWS / "train.jsonl"),
                                {"train.jsonl": checksum(REVIEWS / "train.jsonl")})
