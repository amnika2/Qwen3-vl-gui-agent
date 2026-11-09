import copy
import json
from pathlib import Path

import pytest

from guiqa.rl_review_reward import score_review, validate_gold


@pytest.fixture
def gold():
    return {
        "action": "finish", "task_kind": "edit",
        "field_readings": [{
            "field": "Mobile", "intent": "edit", "requested_value": "+15551234567",
            "before": {"value": "+15551234567", "step": 3},
            "after": {"value": "", "step": 7},
            "input_matches_request": True, "result_matches_request": False,
        }],
        "submission": {"status": "accepted", "evidence": {"step": 4, "observation": "Saved"}},
        "result_observed": True, "result_steps": [7], "expected_rejection": False,
        "verdict": "fail", "observation": "The accepted save leaves Mobile empty.",
        "evidence_steps": [3, 4, 7],
    }


def score(answer, gold, **kwargs):
    return score_review(json.dumps(answer), gold, {3, 4, 7, 8}, **kwargs)


def test_correct_defect_requires_both_conclusion_and_observed_facts(gold):
    assert score(gold, gold)["reward"] == 1.2
    wrong = copy.deepcopy(gold)
    wrong["verdict"] = "blocked"
    result = score(wrong, gold)
    assert result["reward"] == -0.8
    assert not result["verdict_correct"]
    assert result["core_facts_correct"]
    assert "verdict:mismatch" in result["errors"]


def test_self_consistent_fabrication_does_not_receive_positive_reward(gold):
    wrong = copy.deepcopy(gold)
    wrong["field_readings"][0]["after"]["value"] = "+15551234567"
    wrong["field_readings"][0]["result_matches_request"] = True
    wrong["verdict"] = "pass"
    result = score(wrong, gold)
    assert result["guard"] is None
    assert result["reward"] < 0
    assert not result["core_facts_correct"]


def test_visible_empty_is_distinct_from_unobserved(gold):
    wrong = copy.deepcopy(gold)
    wrong["field_readings"][0]["after"] = None
    result = score(wrong, gold)
    assert "field_readings.Mobile.after.value:mismatch" in result["errors"]
    assert result["reward"] < 0


def test_accepted_save_stays_accepted_when_saved_value_is_wrong(gold):
    wrong = copy.deepcopy(gold)
    wrong["submission"]["status"] = "rejected"
    result = score(wrong, gold)
    assert "submission.status:mismatch" in result["errors"]
    assert result["reward"] < 0


def test_correct_blocked_is_rewarded(gold):
    gold.update(verdict="blocked", result_observed=False, result_steps=[])
    gold["submission"] = {"status": "unknown", "evidence": None}
    gold["field_readings"][0].update(before=None, after=None,
                                     input_matches_request=None, result_matches_request=None)
    assert validate_gold(gold, {3, 4, 7}) == gold
    assert score(gold, gold)["reward"] == 1.2


def test_unverified_equivalent_citation_is_deferred_instead_of_penalized(gold):
    candidate = copy.deepcopy(gold)
    candidate["field_readings"][0]["after"]["step"] = 8
    candidate["result_steps"] = [8]
    result = score(candidate, gold)
    assert result["core_facts_correct"]
    assert result["verdict_correct"]
    assert result["reward"] is None
    assert result["provisional_reward"] == 1.2
    assert result["needs_review"] and not result["strict_correct"]
    approved = {"field_readings.Mobile.after.step": [8], "result_steps": [8]}
    reviewed = score(candidate, gold, approved_citations=approved)
    assert reviewed["strict_correct"] and reviewed["reward"] == 1.2


def test_unmatched_field_name_requires_review_without_guessing_synonyms(gold):
    candidate = copy.deepcopy(gold)
    candidate["field_readings"][0]["field"] = "Mobile number"
    result = score(candidate, gold)
    assert result["reward"] is None
    assert result["needs_review"] and not result["strict_correct"]
    assert result["unverified_fields"] == {"missing": ["Mobile"], "extra": ["Mobile number"]}


def test_field_order_case_and_explanatory_prose_do_not_change_reward(gold):
    candidate = copy.deepcopy(gold)
    candidate["field_readings"][0]["field"] = " mobile "
    candidate["observation"] = "The final value differs from the requested value."
    candidate["submission"]["evidence"]["observation"] = "The application visibly accepted the save."
    candidate["evidence_steps"] = [7, 4, 3]
    assert score(candidate, gold)["strict_correct"]


@pytest.mark.parametrize("raw", ["not json", "{}", "[]"])
def test_format_errors_are_negative_and_serializable(raw, gold):
    result = score_review(raw, gold, {3, 4, 7})
    assert result["reward"] == -1
    assert result["parse_error"]
    json.dumps(result)


def test_gold_validation_rejects_a_verdict_contradicting_visible_comparison(gold):
    gold["verdict"] = "pass"
    with pytest.raises(ValueError, match="gold annotation contradicts"):
        validate_gold(gold, {3, 4, 7})


def test_all_frozen_v4_gold_reviews_validate_and_score_themselves():
    dataset = Path(__file__).resolve().parents[1] / "datasets/sft-main-10apps-v4"
    count = 0
    for partition in ("train", "validation"):
        for line in (dataset / f"{partition}.jsonl").read_text().splitlines():
            row = json.loads(line)
            if row["phase"] != "review":
                continue
            steps = {frame["step"] for frame in row["review_frames"]}
            raw = row["messages"][-1]["content"]
            gold = validate_gold(json.loads(raw), steps, row["review_profile"])
            result = score_review(raw, gold, steps, row["review_profile"])
            assert result["strict_correct"], (row["id"], result)
            assert result["reward"] == 1.2
            count += 1
    assert count == 130
