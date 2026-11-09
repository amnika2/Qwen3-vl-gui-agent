import copy
import json
from pathlib import Path

import pytest
from guiqa.rl_review_reward import score_review
from guiqa.rl_reward_rules import load_reward_rules, rules_for_sample


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


@pytest.fixture
def optional_hostname():
    return {
        "field": "Hostname", "intent": "preserve",
        "requested_value": "server1.cust1.local",
        "before": {"value": "server1.cust1.local", "step": 3},
        "after": {"value": "server1.cust1.local", "step": 7},
        "input_matches_request": None, "result_matches_request": True,
    }


def score(answer, gold, rules=None):
    return score_review(json.dumps(answer), gold, {3, 4, 7, 8}, rules=rules)


def test_verified_alias_and_reference_receive_full_reward(gold):
    answer = copy.deepcopy(gold)
    answer["field_readings"][0]["field"] = "Mobile number"
    answer["field_readings"][0]["after"]["step"] = 8
    answer["result_steps"] = [8]
    rules = {
        "field_aliases": {"Mobile number": "Mobile"},
        "approved_citations": {"field_readings.Mobile.after.step": [8], "result_steps": [8]},
    }

    result = score(answer, gold, rules)

    assert result["strict_correct"] and result["reward"] == 1.2


def test_verified_aliases_cannot_hide_duplicate_claims(gold):
    answer = copy.deepcopy(gold)
    duplicate = copy.deepcopy(answer["field_readings"][0])
    duplicate.update(field="Mobile number", after={"value": "invented", "step": 7})
    answer["field_readings"].append(duplicate)

    result = score(answer, gold, {"field_aliases": {"Mobile number": "Mobile"}})

    assert result["reward"] < 0
    assert not result["strict_correct"]
    assert result["errors"]


def test_verified_optional_field_is_allowed_without_becoming_required(gold, optional_hostname):
    rules = {"optional_fields": [optional_hostname]}
    assert score(gold, gold, rules)["strict_correct"]
    answer = copy.deepcopy(gold)
    answer["field_readings"].append(copy.deepcopy(optional_hostname))

    result = score(answer, gold, rules)

    assert result["strict_correct"] and result["reward"] == 1.2
    assert result["unverified_fields"] == {"missing": [], "extra": []}


def test_correct_optional_fields_do_not_increase_partial_credit(gold, optional_hostname):
    answer = copy.deepcopy(gold)
    answer["task_kind"] = "create"
    rules = {"optional_fields": [optional_hostname]}
    without_optional = score(answer, gold, rules)
    answer["field_readings"].append(copy.deepcopy(optional_hostname))

    with_optional = score(answer, gold, rules)

    assert with_optional["partial_fraction"] == without_optional["partial_fraction"]


@pytest.mark.parametrize("key,value", [
    ("input_matches_request", True),
    ("result_matches_request", False),
    ("before", None),
    ("after", {"value": "another.host", "step": 7}),
])
def test_verified_optional_field_does_not_approve_incorrect_facts(
        gold, optional_hostname, key, value):
    answer = copy.deepcopy(gold)
    claimed = copy.deepcopy(optional_hostname)
    claimed[key] = value
    answer["field_readings"].append(claimed)

    result = score(answer, gold, {"optional_fields": [optional_hostname]})

    assert result["reward"] < 0
    assert not result["strict_correct"]
    assert any("field_readings.Hostname." in error for error in result["errors"])


def test_unknown_extra_field_is_deferred(gold, optional_hostname):
    answer = copy.deepcopy(gold)
    answer["field_readings"].append(copy.deepcopy(optional_hostname))

    result = score(answer, gold)

    assert result["reward"] is None
    assert result["needs_review"] and not result["strict_correct"]
    assert result["unverified_fields"]["extra"] == ["Hostname"]


def test_known_value_error_stays_negative_with_unknown_citation_and_extra(gold, optional_hostname):
    answer = copy.deepcopy(gold)
    answer["field_readings"][0]["after"] = {"value": "wrong", "step": 7}
    answer["field_readings"].append(copy.deepcopy(optional_hostname))
    answer["evidence_steps"].append(8)

    result = score(answer, gold)

    assert result["reward"] == -1.0
    assert "field_readings.Mobile.after.value:mismatch" in result["errors"]


def test_different_value_with_its_own_unverified_reference_is_deferred(gold):
    answer = copy.deepcopy(gold)
    answer["field_readings"][0]["after"] = {"value": "wrong", "step": 8}

    result = score(answer, gold)

    assert result["reward"] is None
    assert not result["strict_correct"]
    assert result["needs_review"]


def test_optional_requested_text_difference_does_not_receive_positive_reward(gold, optional_hostname):
    answer = copy.deepcopy(gold)
    claimed = copy.deepcopy(optional_hostname)
    claimed["requested_value"] = "another.host"
    answer["field_readings"].append(claimed)

    result = score(answer, gold, {"optional_fields": [optional_hostname]})

    assert result["reward"] is None or result["reward"] < 0
    assert not result["strict_correct"]


def test_visible_empty_is_not_unobserved_even_with_unverified_extra(gold, optional_hostname):
    answer = copy.deepcopy(gold)
    answer["field_readings"][0]["after"] = None
    answer["field_readings"].append(copy.deepcopy(optional_hostname))

    result = score(answer, gold)

    assert result["reward"] == -1.0
    assert "field_readings.Mobile.after.value:mismatch" in result["errors"]


def test_unobserved_gold_does_not_disprove_a_claim_on_an_unverified_image(gold):
    gold["field_readings"][0].update(before=None, input_matches_request=None)
    gold["verdict"] = "blocked"
    answer = copy.deepcopy(gold)
    answer["field_readings"][0].update(before={"value": "other input", "step": 8},
                                      input_matches_request=False)

    result = score(answer, gold)

    assert result["reward"] is None
    assert not result["strict_correct"]


def test_literal_preservation_target_does_not_approve_reopened_baseline(gold, optional_hostname):
    gold = copy.deepcopy(gold)
    baseline = copy.deepcopy(optional_hostname)
    baseline["requested_value"] = "unchanged"
    gold["field_readings"].append(baseline)
    answer = copy.deepcopy(gold)
    answer["field_readings"][1]["field"] = "Server host"
    answer["field_readings"][1]["requested_value"] = "server1.cust1.local"
    answer["field_readings"][1]["before"]["step"] = 8
    rules = {
        "field_aliases": {"Server host": "Hostname"},
        "approved_requested_values": {"Hostname": ["server1.cust1.local"]},
        "invalid_citations": {"field_readings.Hostname.before.step": [8]},
    }

    result = score(answer, gold, rules)

    assert result["reward"] < 0
    assert not result["strict_correct"]
    assert not any("requested_value:mismatch" in error for error in result["errors"])
    assert any("field_readings.Hostname.before.step" in error for error in result["errors"])


@pytest.mark.parametrize("gold_target,answer_target", [
    ("unchanged", "server1.cust1.local"), ("server1.cust1.local", "unchanged"),
])
def test_preservation_target_matches_the_known_baseline(gold, optional_hostname, gold_target, answer_target):
    optional_hostname["requested_value"] = gold_target
    gold["field_readings"].append(optional_hostname)
    answer = copy.deepcopy(gold)
    answer["field_readings"][-1]["requested_value"] = answer_target

    assert score(answer, gold)["strict_correct"]


def test_preservation_target_does_not_accept_a_different_literal(gold, optional_hostname):
    optional_hostname["requested_value"] = "unchanged"
    gold["field_readings"].append(optional_hostname)
    answer = copy.deepcopy(gold)
    answer["field_readings"][-1]["requested_value"] = "another.host"

    assert not score(answer, gold)["strict_correct"]


@pytest.fixture(scope="module")
def distributed_reviews():
    root = Path(__file__).resolve().parents[1]
    report = root / "reports/rl-review-diagnostic-v1-distributed"
    selected = {row["id"]: row for row in map(json.loads, (report / "selected.jsonl").read_text().splitlines())}
    predictions = {(row["sample_index"], row["candidate_index"]): row
                   for row in map(json.loads, (report / "predictions.jsonl").read_text().splitlines())}
    rules = load_reward_rules(root / "configs/rl-review-reward-rules.yaml")
    dataset = Path(json.loads((report / "manifest.json").read_text())["dataset"])
    return selected, predictions, rules, dataset


@pytest.mark.parametrize("sample_index,candidate_index,expected", [
    (12, 0, "positive"),
    (11, 1, "negative"),
    (0, 0, "negative"),
    (11, 5, "deferred"),
])
def test_actual_distributed_answers_keep_verified_and_unknown_facts_distinct(
        distributed_reviews, sample_index, candidate_index, expected):
    selected, predictions, config, dataset = distributed_reviews
    prediction = predictions[sample_index, candidate_index]
    source = selected[prediction["id"]]
    rules = rules_for_sample(config, source, dataset)
    gold = json.loads(source["messages"][-1]["content"])

    result = score_review(prediction["raw"], gold,
                          {frame["step"] for frame in source["review_frames"]},
                          source["review_profile"], rules=rules)

    if expected == "positive":
        assert result["strict_correct"] and result["reward"] == 1.2
    elif expected == "negative":
        assert result["reward"] < 0
        assert not result["strict_correct"]
    else:
        assert result["reward"] is None
        assert not result["strict_correct"]
        assert "MAC address" in result["unverified_fields"]["extra"]


def test_training_callback_uses_the_same_verified_rules(distributed_reviews, tmp_path):
    from guiqa.train_review_grpo import ReviewReward

    selected, predictions, config, dataset = distributed_reviews
    prediction = predictions[12, 0]
    source = selected[prediction["id"]]
    sample = {"id": source["id"], "gold": json.loads(source["messages"][-1]["content"]),
              "steps": {frame["step"] for frame in source["review_frames"]},
              "profile": source["review_profile"],
              "reward_rules": rules_for_sample(config, source, dataset)}
    reward = ReviewReward([sample], tmp_path, {99})

    values = reward([[{"content": prediction["raw"]}]] * 4, [sample["id"]] * 4, [[10, 99]] * 4)

    assert values == [1.2] * 4
    assert not reward.groups[0]["dropped_untrusted_group"]


def test_truncation_is_deferred_by_both_replay_and_training(gold, tmp_path):
    from guiqa.rl_review_reward import resolve_training_reward
    from guiqa.train_review_grpo import ReviewReward

    raw = '{"action":"finish"'
    replay_score = score_review(raw, gold, {3, 4, 7}, truncated=True)
    assert replay_score["reward"] is None and not replay_score["strict_correct"]
    assert resolve_training_reward(replay_score)["reward"] is None
    sample = {"id": "sample", "gold": gold, "steps": {3, 4, 7},
              "profile": "persistence", "reward_rules": {}}
    reward = ReviewReward([sample], tmp_path, {99})
    answers = [[{"content": json.dumps(gold)}]] * 3 + [[{"content": raw}]]

    values = reward(answers, ["sample"] * 4, [[10, 99]] * 3 + [[10]])

    assert values == [0.0] * 4
    assert reward.groups[0]["truncated_candidates"] == 1
    assert reward.groups[0]["untrusted_candidates"] == 1
    assert reward.groups[0]["dropped_untrusted_group"]
