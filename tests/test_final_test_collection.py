from collections import Counter
from types import SimpleNamespace

from guiqa.final_test_catalog import build_catalog
from guiqa.final_test_collection import make_label


def test_registered_test_and_calibration_families_are_disjoint_and_keep_pairs_together():
    catalog = build_catalog()
    test = [group for group in catalog["groups"] if not group["calibration"]]
    calibration = [group for group in catalog["groups"] if group["calibration"]]
    assert len(test) == 100 and sum(len(group["builds"]) for group in test) == 150
    assert len(calibration) == 25 and sum(len(group["builds"]) for group in calibration) == 40
    assert not ({group["content_key"] for group in test} & {group["content_key"] for group in calibration})
    assert Counter(group["app"] for group in test) == {app: 20 for app in ("akaunting", "ampache", "anchor", "appointments", "emlog")}
    assert all(group["builds"] == ["A", "B"] for group in test if group["kind"] == "target")


def test_saved_value_and_visible_input_determine_labels_instead_of_build():
    fixture = {"field": "mobile_number", "expected": "+15553000001"}
    group = {"app": "appointments", "variant": "saved", "workflow": "target"}
    snapshots = {
        0: {"fields": {"Mobile Number": None}},
        1: {"fields": {"Mobile Number": {"value": fixture["expected"]}}},
        2: {"fields": {"Mobile Number": None}, "messages": ["Provider saved successfully."]},
        3: {"fields": {"Mobile Number": {"value": ""}}},
    }
    context = SimpleNamespace(visible=snapshots, observer=SimpleNamespace(evidence=SimpleNamespace(response_valid=True)))
    answer, steps, profile = make_label(context, fixture, group, 2)
    assert answer["verdict"] == "fail" and answer["submission"]["status"] == "accepted"
    assert answer["field_readings"][0]["after"] == {"value": "", "step": 3}
    snapshots[1]["fields"]["Mobile Number"]["value"] = ""
    group["variant"] = "input_not_established"
    blocked, _, _ = make_label(context, fixture, group, 2)
    assert blocked["verdict"] == "blocked"
    assert blocked["field_readings"][0]["input_matches_request"] is False


def test_automatic_creation_transcribes_the_reopened_name_and_keeps_the_public_naming_rule():
    fixture = {"category": "preservation", "username": "viewer"}
    group = {"app": "ampache", "variant": "saved", "workflow": "preservation"}
    context = SimpleNamespace(visible={
        0: {"fields": {"Name": None}},
        1: {"fields": {"Name": None}, "messages": [],
            "body_text": "Your search has been saved as a Smart Playlist"},
        2: {"fields": {"Name": {"value": "viewer - 11/8/26"}}}})
    answer, _, _ = make_label(context, fixture, group, 1)
    assert answer["verdict"] == "pass"
    assert answer["field_readings"][0]["requested_value"] == "automatic name beginning with the signed-in username"
    assert answer["field_readings"][0]["after"] == {"value": "viewer - 11/8/26", "step": 2}
