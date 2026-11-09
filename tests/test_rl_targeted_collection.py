from argparse import Namespace

import pytest

from guiqa.rl_targeted_collection import (ROOT, collection_jobs, field_label,
                                         label_from_visible, load_catalog, task_fixture,
                                         wolf_label_from_visible, input_not_established_flow,
                                         verify_unestablished_input, shard_jobs)
from guiqa.oracle import Evidence
from guiqa.review_contract import parse_review, review_guard
from guiqa.sft_flows import SftFlow


def test_matched_builds_keep_the_same_group_partition_and_fixture():
    catalog = load_catalog(ROOT / "configs/rl-targeted-curriculum.yaml")
    catalog["groups"] = [group for group in catalog["groups"] if group["app"] != "wolf"]
    jobs = collection_jobs(catalog, Namespace(app=None, calibration=False, include_blocked=True,
                                            input_not_established_only=False))
    saved = [(group, build) for group, build, variant in jobs if variant == "saved"]
    assert len(saved) == 24
    assert len(jobs) == 32
    for group in catalog["groups"]:
        pair = [(entry, build) for entry, build in saved if entry == group]
        assert [build for _, build in pair] == ["A", "B"]
        assert pair[0][0]["split"] == pair[1][0]["split"]
        assert task_fixture(pair[0][0]) == task_fixture(pair[1][0])


def test_weight_label_keeps_literal_display_but_compares_numeric_value():
    result = field_label("Weight", "edit", "0.35", {"value": "0.35"}, 3,
                         {"value": "+0.350"}, 8, numeric=True)
    assert result["after"] == {"value": "+0.350", "step": 8}
    assert result["result_matches_request"] is True
    assert field_label("Weight", "edit", "0.35", {"value": "0.35"}, 3,
                       {"value": "+0"}, 8, numeric=True)["result_matches_request"] is False


def test_unseen_input_is_rejected_but_visible_unmatched_input_is_recorded():
    with pytest.raises(ValueError, match="outside"):
        field_label("Description", "edit", "", None, 3, {"value": "old"}, 8)
    reading = field_label("Description", "edit", "", {"value": "old"}, 3, {"value": "old"}, 8)
    assert reading["before"] == {"value": "old", "step": 3}
    assert reading["input_matches_request"] is False


def test_successful_transport_without_visible_success_is_not_gold_acceptance():
    group = {"app": "typesetter"}
    fixture = {"input": "Final"}
    visible = {
        0: {"fields": {"Header": {"value": "Original"}}, "messages": []},
        3: {"fields": {"Header input": {"value": "Final"}}, "messages": []},
        4: {"fields": {}, "messages": []},
        5: {"fields": {"Header": {"value": "Old"}}, "messages": []},
    }
    flow = SftFlow([], 3, 4)
    with pytest.raises(ValueError, match="no visible response"):
        label_from_visible(visible, flow, group, fixture, "saved", "fail")
    visible[4]["messages"] = [{"value": "Your changes were saved."}]
    label, steps = label_from_visible(visible, flow, group, fixture, "saved", "fail")
    assert label["verdict"] == "fail"
    assert label["submission"]["status"] == "accepted"
    assert steps == [0, 3, 4, 5]
    with pytest.raises(ValueError, match="disagree"):
        label_from_visible(visible, flow, group, fixture, "saved", "pass")


def test_cancelled_edit_is_blocked_even_when_old_value_is_visible():
    group = {"app": "opencart"}
    fixture = {"values": {"weight": "0.35"}}
    visible = {
        0: {"fields": {"Weight": {"value": "+20"}}, "messages": []},
        3: {"fields": {"Weight input": {"value": "0.35"}}, "messages": []},
        4: {"fields": {}, "messages": []},
        5: {"fields": {"Weight": {"value": "+20"}}, "messages": []},
    }
    label, _ = label_from_visible(visible, SftFlow([], 3, 0), group, fixture, "cancelled", "inconclusive")
    assert label["verdict"] == "blocked"
    assert label["submission"] == {"status": "unknown", "evidence": None}
    assert label["field_readings"][0]["result_matches_request"] is False


def test_wolf_jobs_keep_expected_rejection_controls_out_of_cancelled_variants():
    catalog = load_catalog(ROOT / "configs/rl-targeted-curriculum.yaml")
    args = Namespace(app=["wolf"], calibration=False, include_blocked=True, input_not_established_only=False)
    jobs = collection_jobs(catalog, args)
    assert len(jobs) == 14
    assert all(variant == "saved" for _, _, variant in jobs)
    assert sum(group["split"] == "train" for group, _, _ in jobs) == 10
    args.calibration = True
    assert [(group["fixture"]["kind"], build) for group, build, _ in collection_jobs(catalog, args)] == [
        ("edit", "A"), ("edit", "B"), ("duplicate", "A"), ("duplicate", "B")]


def test_same_visible_rejection_has_opposite_verdict_for_existing_and_duplicate_tasks():
    import json

    visible = {
        1: {"fields": {"Title": {"value": "Birch Archive"}, "Slug": None}, "messages": []},
        2: {"fields": {"Title": None, "Slug": {"value": "about-us"}}, "messages": []},
        4: {"fields": {"Title": None, "Slug": None}, "messages": [
            {"value": "Page with slug about-us already exists!"}]},
    }
    for kind, verdict in (("edit", "fail"), ("duplicate", "pass")):
        label, steps = wolf_label_from_visible(visible, SftFlow([], 2, 4),
                                              {"kind": kind, "fields": {"title": "Birch Archive"}}, verdict)
        assert label["submission"]["status"] == "rejected"
        assert label["expected_rejection"] is (kind == "duplicate")
        assert label["verdict"] == verdict
        assert label["result_observed"] is False
        parse_review(json.dumps(label), set(steps), "business_outcome_v1")
        assert review_guard(label, "business_outcome_v1") is None
    visible[4]["messages"] = []
    label, _ = wolf_label_from_visible(visible, SftFlow([], 2, 4),
                                      {"kind": "edit", "fields": {"title": "Birch Archive"}}, "fail")
    assert label["submission"]["status"] == "unknown"
    assert label["verdict"] == "blocked"


def test_unestablished_input_acceptance_is_blocked_and_not_a_persistence_failure():
    group = {"app": "phpipam"}
    fixture = {"description": "Original", "hostname": "host.lab.local", "address": "10.10.1.3",
               "changes": {"description": ""}}
    fields = {"Description": {"value": "Original"}, "Hostname": {"value": "host.lab.local"}}
    visible = {2: {"fields": fields, "messages": []}, 5: {"fields": fields, "messages": []},
               6: {"fields": {"Description": None, "Hostname": None}, "messages": [],
                   "address_row": {"value": "10.10.1.3 host.lab.local Original"}},
               8: {"fields": fields, "messages": []}}
    label, _ = label_from_visible(visible, SftFlow([], 5, 6), group, fixture,
                                  "input_not_established", "inconclusive")
    assert label["submission"]["status"] == "accepted"
    assert label["field_readings"][0]["input_matches_request"] is False
    assert label["result_observed"] is True
    assert label["verdict"] == "blocked"
    with pytest.raises(ValueError, match="visible input does not match"):
        label_from_visible(visible, SftFlow([], 5, 6), group, fixture, "saved", "fail")


def test_unestablished_input_flow_preserves_save_and_reopen_and_original_flow():
    from guiqa.sft_flows import press

    original = SftFlow([press("Control+a"), press("Backspace"), press("Enter"), press("Control+r")], 2, 3)
    changed = input_not_established_flow(original)
    assert original.steps[1](None) == {"action": "press", "key": "Backspace"}
    assert [action(None)["key"] for action in changed.steps] == ["Control+a", "ArrowRight", "Enter", "Control+r"]
    assert changed.input_step == 2 and changed.submit_step == 3


def test_unestablished_input_requires_successful_same_record_oracle_evidence():
    import json

    fixture = {"description": "Original", "hostname": "host.lab.local"}
    evidence = Evidence(preconditions=True, submitted=False, response_valid=True, reopened=True,
                        same_object=True, actual=json.dumps(fixture, sort_keys=True))
    verify_unestablished_input(evidence, fixture)
    evidence.response_valid = False
    with pytest.raises(ValueError, match="accepted original-value"):
        verify_unestablished_input(evidence, fixture)


def test_unestablished_input_jobs_use_the_same_declared_phpipam_partitions():
    catalog = load_catalog(ROOT / "configs/rl-targeted-curriculum.yaml")
    args = Namespace(app=["phpipam"], calibration=False, include_blocked=False, input_not_established_only=True)
    jobs = collection_jobs(catalog, args)
    assert len(jobs) == 8
    assert {variant for _, _, variant in jobs} == {"input_not_established"}
    assert sum(group["split"] == "train" for group, _, _ in jobs) == 6
    args.calibration = True
    assert [(group["id"], build) for group, build, _ in collection_jobs(catalog, args)] == [("birch", "A"), ("birch", "B")]


def test_collection_shards_keep_each_parent_and_all_its_variants_together():
    catalog = load_catalog(ROOT / "configs/rl-targeted-curriculum.yaml")
    jobs = collection_jobs(catalog, Namespace(app=None, calibration=False, include_blocked=True,
                                            input_not_established_only=False))
    shards = [shard_jobs(jobs, index, 2) for index in range(2)]
    parents = [{(group["app"], group["id"]) for group, _, _ in shard} for shard in shards]
    assert not parents[0] & parents[1]
    key = lambda job: (job[0]["app"], job[0]["id"], job[1], job[2])
    assert sorted(map(key, shards[0] + shards[1])) == sorted(map(key, jobs))
