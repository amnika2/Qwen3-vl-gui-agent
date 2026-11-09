from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from guiqa.expanded_training_collection import load_catalog, make_label, prepare_task, scenario_identity
from guiqa.review_scenarios import scenario_metadata


ROOT = Path(__file__).resolve().parents[1]


def test_registered_extended_collection_has_the_requested_families_and_178_runs():
    groups = load_catalog(ROOT / "configs/review-expansion-extended-v1.yaml")
    counts = Counter((group["app"], group["data_split"]) for group in groups for _ in group["builds"])
    assert counts == {("mantis", "train"): 40, ("mantis", "validation"): 6,
                      ("impress", "train"): 40, ("impress", "validation"): 6,
                      ("litecart", "train"): 40, ("litecart", "validation"): 6,
                      ("dokuwiki", "train"): 20, ("flatpress", "train"): 20}
    partitions = {split: {group["parent_scenario_id"] for group in groups if group["data_split"] == split}
                  for split in ("train", "validation")}
    assert not partitions["train"] & partitions["validation"]


def test_registered_business_content_keys_do_not_cross_the_partition():
    groups = load_catalog(ROOT / "configs/review-expansion-extended-v1.yaml")
    keys = {"train": set(), "validation": set()}
    for group in groups:
        _, task, initial, requested = prepare_task(group)
        metadata = scenario_metadata(group["app"], group["workflow"], task, initial, requested,
                                     group["parent_scenario_id"])
        keys[group["data_split"]].add(metadata["content_key"])
    assert not keys["train"] & keys["validation"]


def test_visible_category_prefix_is_preserved_and_rejected_fields_remain_unseen():
    visible = {1: {"fields": {"Category": {"value": "[All Projects] General"}}, "application_error": False},
               2: {"fields": {"Category": None}, "application_error": True}}
    observer = SimpleNamespace(evidence=SimpleNamespace(events=[{"kind": "submit_response", "step": 2, "accepted": False}]))
    context = SimpleNamespace(visible=visible, observer=observer,
                              history=[{"step": 1, "action": {"action": "type"}}])
    answer, _ = make_label(context, {"app": "mantis"}, {}, {"category": "General"}, 2, "fail")
    field = answer["field_readings"][0]
    assert field["before"]["value"] == "[All Projects] General"
    assert field["input_matches_request"] is True
    assert field["after"] is None
    assert answer["verdict"] == "fail"


def test_synthetic_text_page_names_do_not_change_the_business_content_identity():
    groups = load_catalog(ROOT / "configs/review-expansion-extended-v1.yaml")
    for app in ("dokuwiki", "flatpress"):
        group = next(group for group in groups if group["app"] == app)
        fixture, task, initial, requested = prepare_task(group)
        before, _ = scenario_identity(group, fixture, task, initial, requested)
        renamed = {**group, "parent_scenario_id": group["parent_scenario_id"] + "-renamed"}
        fixture, task, initial, requested = prepare_task(renamed)
        after, identity_task = scenario_identity(renamed, fixture, task, initial, requested)
        assert before["content_key"] == after["content_key"]
        assert fixture["page_id"] in task
        assert fixture["page_id"] not in identity_task


@pytest.mark.parametrize("saved_rows,expected", [(None, "blocked"), ({"value": ""}, "fail")])
def test_saved_country_table_empty_and_outside_the_image_remain_different(saved_rows, expected):
    visible = {1: {"fields": {"Saved country rows input": {"value": "Afghanistan"}}, "application_error": False},
               2: {"fields": {}, "application_error": False},
               3: {"fields": {"Saved country rows": saved_rows}, "application_error": False}}
    observer = SimpleNamespace(evidence=SimpleNamespace(events=[{"kind": "submit_response", "step": 2, "accepted": True}]))
    context = SimpleNamespace(visible=visible, observer=observer,
                              history=[{"step": 1, "action": {"action": "type"}}])
    answer, _ = make_label(context, {"app": "litecart"}, {}, {"country": "Afghanistan"}, 2, "fail")
    assert answer["verdict"] == expected
    after = answer["field_readings"][0]["after"]
    assert after is None if saved_rows is None else after["value"] == ""


def test_wiki_review_asks_only_for_content_while_real_fixture_keeps_metadata_actions():
    groups = load_catalog(ROOT / "configs/review-expansion-extended-v1.yaml")
    group = next(group for group in groups if group["app"] == "dokuwiki" and group["scenario"].get("minor"))
    fixture, task, _, requested = prepare_task(group)
    assert fixture["minor"] is True and fixture["summary"]
    assert "Edit summary" not in task and "Minor Changes" not in task
    assert set(requested) == {"content"}


def test_partial_text_edits_give_the_pre_run_content_baseline_in_the_public_task():
    groups = load_catalog(ROOT / "configs/review-expansion-extended-v1.yaml")
    checked = set()
    for group in groups:
        if group["app"] in {"dokuwiki", "flatpress"} and group["scenario"]["edit"] in {"append", "replace_suffix", "keep"}:
            fixture, task, _, _ = prepare_task(group)
            assert task.startswith("Initial Content before this task is ")
            assert fixture["initial"] in task
            checked.add(group["scenario"]["edit"])
    assert checked == {"append", "replace_suffix", "keep"}
