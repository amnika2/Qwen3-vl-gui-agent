import json

import pytest

from guiqa.business_review import parse_review, review_guard
from guiqa.dataset26_review_labels import normalize_review, public_requirements


@pytest.mark.parametrize(("task", "expected"), [
    ("The existing page should save with its own slug.", "fail"),
    ("Use Save to verify that duplicate slugs are rejected.", "pass"),
])
def test_wolf_visible_slug_rejection_is_a_business_result(task, expected):
    recorded = {"observation": "Visible response: Page with slug about-us already exists!",
                "evidence_steps": [3, 5, 6], "checks": [
                    {"field": "Title", "intent": "edit", "before": {"value": "Revised title", "step": 3},
                     "after": None, "result_matches_request": None},
                    {"field": "Slug", "intent": "preserve", "before": {"value": "about-us", "step": 5},
                     "after": None, "result_matches_request": None},
                ]}
    answer, requirements = normalize_review("sweeper-195", task, recorded)
    assert answer["verdict"] == expected
    assert all(item["status"] == expected for item in answer["checks"])
    parse_review(json.dumps(answer), {3, 5, 6}, requirements=requirements)
    assert review_guard(answer, requirements=requirements) is None
    facts = json.loads(answer["checks"][0]["observed"])
    assert facts["after"] is None
    assert facts["before"]["value"] == "Revised title"
    assert "already exists!" in facts["submission_observation"]


def test_dotclear_excluded_entries_have_distinct_public_ids():
    task = "Inspect titles: 'Required entry' must be present and 'Wrong entry', 'Welcome to Dotclear' must be absent."
    requirements = public_requirements("sweeper-037", task)
    assert [item["id"] for item in requirements] == ["required_entry", "excluded_entry_1", "excluded_entry_2"]
    recorded = {"observation": "Required entry and Wrong entry are visible.", "evidence_steps": [6], "checks": [
        {"name": "required_entry", "expected": "Required entry", "actual": "Required entry\nWrong entry", "matched": True, "step": 6},
        {"name": "excluded_entry", "expected": "Wrong entry", "actual": "Required entry\nWrong entry", "matched": False, "step": 6},
        {"name": "excluded_entry", "expected": "Welcome to Dotclear", "actual": "Required entry\nWrong entry", "matched": True, "step": 6},
    ]}
    answer, declared = normalize_review("sweeper-037", task, recorded)
    assert declared == requirements
    assert [item["status"] for item in answer["checks"]] == ["pass", "fail", "pass"]
    assert answer["verdict"] == "fail"


@pytest.mark.parametrize(("account", "observed", "verdict"), [
    ("inactive", "dashboard", "fail"),
    ("inactive", "credentials rejection", "pass"),
    ("active", "dashboard", "pass"),
    ("active", "credentials rejection", "fail"),
])
def test_authentication_labels_compare_observed_outcome_with_public_account_state(account, observed, verdict):
    recorded = {"observation": observed, "evidence_steps": [0, 7], "checks": [{"observed": observed}]}
    answer, requirements = normalize_review("sweeper-087", f"The InvoicePlane account is {account}.", recorded)
    assert answer["verdict"] == verdict
    assert json.loads(answer["checks"][0]["observed"]) == {"authentication_result": observed}
    parse_review(json.dumps(answer), {0, 7}, requirements=requirements)


def test_distinct_bookmark_preservation_does_not_require_a_duplicate_count():
    task = "Import distinct bookmark URLs; exactly two bookmarks must remain after URL normalization."
    recorded = {"observation": "Two distinct bookmarks are visible.", "evidence_steps": [5], "checks": [
        {"name": "matching_bookmark_count", "expected": 2, "actual": 2, "matched": True, "step": 5}]}
    answer, requirements = normalize_review("sweeper-102", task, recorded)
    assert answer["verdict"] == "pass"
    assert "required by the public task" in requirements[0]["expected"]
    assert "one" not in requirements[0]["expected"].split()
    assert json.loads(answer["checks"][0]["observed"]) == {"actual": 2}


def test_quantity_check_also_identifies_a_nonvariant_product():
    requirements = public_requirements("sweeper-045", "Open the ordinary product and inspect Qty 15 without saving.")
    assert requirements[0]["object"] == "Product Qty input"


def test_file_review_adds_recorded_input_and_submit_frames_without_fabricating_readings():
    recorded = {"observation": "The reopened attachment link is absent.", "evidence_steps": [0, 5],
                "checks": [{"field": "ID Photo", "expected_filename": "evidence.txt", "saved_link_visible": False}]}
    answer, requirements = normalize_review("sweeper-001", "Upload evidence.txt and inspect its saved link.", recorded)
    assert answer["evidence_steps"] == [0, 2, 3, 5]
    assert answer["checks"][0]["evidence_steps"] == [0, 2, 3, 5]
    assert json.loads(answer["checks"][0]["observed"]) == {"saved_attachment_link_visible": False}
    assert answer["verdict"] == "fail"
    parse_review(json.dumps(answer), {0, 2, 3, 5}, requirements=requirements)


def test_delete_review_supplies_original_list_selected_target_and_removal_response():
    recorded = {"observation": "Deletion completed and the other reminder remains.", "evidence_steps": [5, 7], "checks": [
        {"field": "Deletion confirmation", "expected": True, "actual": True, "step": 5},
        {"field": "Selected reminder absent", "expected": True, "actual": True, "step": 7},
        {"field": "Unselected reminder retained", "expected": True, "actual": True, "step": 7},
    ]}
    answer, _ = normalize_review("sweeper-055", "Delete only the selected reminder.", recorded)
    assert answer["evidence_steps"] == [2, 3, 4, 5, 7]
    assert answer["checks"][0]["evidence_steps"] == [2, 3, 4, 5]
    assert answer["verdict"] == "pass"
