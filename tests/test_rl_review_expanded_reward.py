import copy
import json
from pathlib import Path

import pytest

from guiqa.review_facts import to_facts
from guiqa.rl_review_expanded_reward import ExpandedFactsReward, expanded_rules, group_task_rewards
from guiqa.rl_reward_rules import load_reward_rules


ROOT = Path(__file__).resolve().parents[1]


def sample(identifier="sample"):
    gold = {
        "action": "finish", "task_kind": "edit",
        "field_readings": [{"field": "Name", "intent": "edit", "requested_value": "new",
                            "before": {"value": "new", "step": 2},
                            "after": {"value": "new", "step": 6},
                            "input_matches_request": True, "result_matches_request": True}],
        "submission": {"status": "accepted", "evidence": {"step": 3, "observation": "Saved"}},
        "result_observed": True, "result_steps": [6], "expected_rejection": False,
        "verdict": "pass", "observation": "The reopened value is new.", "evidence_steps": [2, 3, 6],
    }
    return {"id": identifier, "case_id": "application", "gold": gold,
            "steps": {2, 3, 6}, "profile": "persistence", "reward_rules": {}}


@pytest.mark.parametrize("source,expected", [
    ([1.0, -1.0, None, None], [1.0, -1.0, 0.0, 0.0]),
    ([1.0, 1.0, -1.0, None], [1.0, 1.0, -1.0, 1.0 / 3]),
    ([None] * 4, [0.0] * 4),
    ([-1.0] * 4, [-1.0] * 4),
])
def test_unknown_has_zero_task_advantage_and_known_candidates_are_retained(source, expected):
    returned = group_task_rewards(source)
    assert returned == expected
    mean = sum(returned) / 4
    for original, numeric in zip(source, returned):
        if original is None:
            assert numeric - mean == pytest.approx(0.0)
        else:
            assert numeric == original


def test_three_g4_questions_are_scored_independently_and_unknown_is_logged(tmp_path):
    samples = [sample(str(index)) for index in range(3)]
    positive = to_facts(samples[0]["gold"])
    negative = copy.deepcopy(positive)
    negative["verdict"] = "fail"
    unknown = copy.deepcopy(positive)
    unknown["field_readings"][0]["field"] = "unverified synonym"
    raw = [json.dumps(answer) for answer in [positive, negative, unknown, positive]
           + [unknown] * 4 + [positive] * 4]
    reward = ExpandedFactsReward(samples, tmp_path, {99})
    completions = [[{"role": "assistant", "content": answer}] for answer in raw]
    tokens = [[0, 99]] * 12
    tokens[3] = [0, 98]

    values = reward(completions, [str(index) for index in range(3) for _ in range(4)], tokens)

    assert values == [1.0, -1.0, 0.0, 0.0] + [0.0] * 4 + [1.0] * 4
    assert [group["known_candidates"] for group in reward.groups] == [2, 0, 4]
    assert [group["trusted_reward_difference"] for group in reward.groups] == [True, False, False]
    rows = [json.loads(line) for line in (tmp_path / "rollouts.jsonl").read_text().splitlines()]
    assert len(rows) == 12
    assert rows[2]["source_reward"] is None and rows[2]["unknown"]
    assert rows[3]["score"]["transport_error"] == "length_limit_without_eos"


def test_alternate_citations_require_selected_same_page_same_value_and_correct_phase(tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    snapshots = {str(step): {"url": "/record/1", "fields": {"Name": {"value": "new", "in_view": True}}}
                 for step in range(1, 10)}
    snapshots["5"]["fields"]["Name"]["value"] = "different"
    snapshots["8"]["url"] = "/record/2"
    snapshots["9"]["fields"]["Name"]["in_view"] = False
    (private / "visible.json").write_text(json.dumps(snapshots))
    gold = sample()["gold"]
    row = {"id": "new", "case_id": "application", "run_directory": str(tmp_path),
           "derived_provenance": {"source_run_id": "new"},
           "review_frames": [{"step": step} for step in range(1, 10)],
           "messages": [{"role": "assistant", "content": json.dumps(gold)}]}
    catalog = {"case_aliases": {}, "samples": {
        "another-run": {"case_id": "application", "field_aliases": {"unproven label": "Name"}}}}

    rules = expanded_rules(row, tmp_path, catalog)

    assert rules["approved_citations"] == {"field_readings.Name.after.step": [7],
                                           "evidence_steps": [7], "result_steps": [7]}
    assert "submission.evidence.step" not in rules["approved_citations"]
    assert "unproven label" not in rules["field_aliases"]
    assert rules["expanded_evidence"]["selected_steps"] == list(range(1, 10))
    item = sample()
    item["steps"] = set(range(1, 10))
    item["reward_rules"] = rules
    replacement = to_facts(gold)
    replacement["field_readings"][0]["after"]["step"] = 7
    replacement["result_steps"] = [7]
    replacement["evidence_steps"] = [2, 3, 7]
    reward = ExpandedFactsReward([item], tmp_path, {99})
    _, resolutions = reward.score_answers(item, [json.dumps(replacement)], [[0, 99]])
    assert resolutions[0]["reward"] == 1.0


def test_source_mapped_litecart_country_and_city_labels_receive_the_gold_reward(tmp_path):
    dataset = ROOT / "datasets/rl-review-expanded-v1"
    rows = [json.loads(line) for line in (dataset / "train.jsonl").read_text().splitlines()]
    row = next(row for row in rows if row["case_id"] == "sweeper-105"
               and row.get("run_directory") and "extended" in row["run_directory"])
    catalog = load_reward_rules(ROOT / "configs/rl-review-reward-rules-retry.yaml")
    item = {"id": row["id"], "case_id": row["case_id"],
            "gold": json.loads(row["messages"][-1]["content"]), "profile": row["review_profile"],
            "steps": {frame["step"] for frame in row["review_frames"]},
            "reward_rules": expanded_rules(row, dataset, catalog)}
    answer = to_facts(item["gold"])
    for field in answer["field_readings"]:
        field["field"] = {"Saved country rows": "Country", "Saved city rows": "City"}.get(
            field["field"], field["field"])
    reward = ExpandedFactsReward([item], tmp_path, {99})

    _, resolutions = reward.score_answers(item, [json.dumps(answer)], [[0, 99]])

    assert resolutions[0]["reward"] == 1.0
    assert "expanded_training_collection.py:visible_fields" in item["reward_rules"]["expanded_evidence"]["field_alias_source"]


def test_all_655_training_golds_remain_positive_under_the_expanded_rules(tmp_path):
    dataset = ROOT / "datasets/rl-review-expanded-v1"
    rows = [json.loads(line) for line in (dataset / "train.jsonl").read_text().splitlines()]
    catalog = load_reward_rules(ROOT / "configs/rl-review-reward-rules-retry.yaml")
    samples = [{"id": row["id"], "case_id": row["case_id"],
                "gold": json.loads(row["messages"][-1]["content"]),
                "profile": row["review_profile"], "steps": {frame["step"] for frame in row["review_frames"]},
                "reward_rules": expanded_rules(row, dataset, catalog)} for row in rows]
    reward = ExpandedFactsReward(samples, tmp_path, {99})

    assert len(samples) == 655
    for item in samples:
        answer = json.dumps(to_facts(item["gold"]))
        _, resolutions = reward.score_answers(item, [answer], [[0, 99]])
        assert resolutions[0]["reward"] == 1.0, item["id"]
