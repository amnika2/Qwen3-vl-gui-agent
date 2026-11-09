import hashlib
import json
from pathlib import Path

import pytest
import yaml

from guiqa.rl_reward_rules import load_reward_rules, rules_for_sample


@pytest.fixture
def bound_rules(tmp_path):
    before, after = b"before screenshot", b"after screenshot"
    (tmp_path / "before.png").write_bytes(before)
    (tmp_path / "after.png").write_bytes(after)
    row = {
        "id": "run:review:evidence_v2", "case_id": "test-case",
        "derived_provenance": {"source_run_id": "run"},
        "review_frames": [{"step": 3}, {"step": 7}],
        "messages": [
            {"role": "user", "content": [
                {"type": "image", "image": "before.png"},
                {"type": "image", "image": "after.png"},
            ]},
            {"role": "assistant", "content": "{}"},
        ],
    }
    catalog = {
        "version": 1, "case_aliases": {"test-case": {"Page Title": "Title"}},
        "samples": {"run": {
            "case_id": "test-case", "sample_id": row["id"],
            "field_aliases": {"Metadata Slug": "Slug"},
            "approved_citations": {"result_steps": [7]},
            "evidence_images": {
                3: hashlib.sha256(before).hexdigest(),
                7: hashlib.sha256(after).hexdigest(),
            },
        }},
    }
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(catalog))
    return path, row, tmp_path


def test_catalog_load_and_merge_bind_the_actual_frame_order(bound_rules):
    path, row, dataset = bound_rules
    catalog = load_reward_rules(path)

    rules = rules_for_sample(catalog, row, dataset)

    assert rules["field_aliases"] == {"Page Title": "Title", "Metadata Slug": "Slug"}
    assert rules["approved_citations"] == {"result_steps": [7]}


@pytest.mark.parametrize("key,value", [("case_id", "another-case"),
                                       ("id", "run:review:evidence_v2:evidence_v2")])
def test_rules_reject_a_different_case_or_sample_without_prefix_matching(bound_rules, key, value):
    path, row, dataset = bound_rules
    row[key] = value
    with pytest.raises(ValueError, match="case_id and sample_id"):
        rules_for_sample(load_reward_rules(path), row, dataset)


def test_rules_reject_images_permuted_between_steps(bound_rules):
    path, row, dataset = bound_rules
    row["messages"][0]["content"].reverse()
    with pytest.raises(ValueError, match="image changed at step 3"):
        rules_for_sample(load_reward_rules(path), row, dataset)


def test_rule_cannot_approve_a_step_without_its_image_hash(bound_rules):
    path, _, _ = bound_rules
    catalog = yaml.safe_load(path.read_text())
    catalog["samples"]["run"]["approved_citations"]["result_steps"] = [8]
    path.write_text(yaml.safe_dump(catalog))
    with pytest.raises(ValueError, match="without an evidence hash"):
        load_reward_rules(path)


def test_unconfigured_run_receives_only_case_aliases(bound_rules):
    path, row, dataset = bound_rules
    row["derived_provenance"]["source_run_id"] = "unreviewed-run"
    assert rules_for_sample(load_reward_rules(path), row, dataset) == {
        "field_aliases": {"Page Title": "Title"},
    }


def test_saved_catalog_matches_all_51_actual_pilot_sample_ids_and_images():
    root = Path(__file__).resolve().parents[1]
    dataset = root / "datasets/rl-review-pilot-v1"
    catalog = load_reward_rules(root / "configs/rl-review-reward-rules.yaml")
    rows = [json.loads(line) for line in (dataset / "train.jsonl").read_text().splitlines()]
    matched = 0
    for row in rows:
        if row["derived_provenance"]["source_run_id"] not in catalog["samples"]:
            continue
        rules = rules_for_sample(catalog, row, dataset)
        assert rules["sample_id"] == row["id"]
        matched += 1
    assert matched == len(catalog["samples"]) == 51
