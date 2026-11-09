import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guiqa.rl_review_retry import binary_training_reward, group_eligible, retry_trainer
from guiqa.rl_review_reward import score_review, validate_gold
from guiqa.rl_reward_rules import load_reward_rules, rules_for_sample


ROOT = Path(__file__).resolve().parents[1]


class NoProvisional(dict):
    def __getitem__(self, key):
        if key == "provisional_reward":
            raise AssertionError("retry training must not use provisional rewards")
        return super().__getitem__(key)


@pytest.mark.parametrize("score", [
    NoProvisional(reward=None, strict_correct=False, provisional_reward=1.2,
                  unverified_fields={"missing": [], "extra": ["Unknown field"]}),
    NoProvisional(reward=None, strict_correct=False, provisional_reward=-1.0,
                  transport_error="length_limit_without_eos"),
])
def test_unknown_or_truncated_answers_remain_unscored(score):
    assert binary_training_reward(score) is None
    assert not group_eligible([1.0, -1.0, -1.0, binary_training_reward(score)])


@pytest.mark.parametrize("rewards", [[1.0] * 4, [-1.0] * 4, [None] * 4])
def test_groups_without_a_reliable_correct_incorrect_comparison_do_not_train(rewards):
    assert not group_eligible(rewards)


def test_actual_all_wrong_partial_credit_group_loses_its_false_preference_signal():
    rollouts = [json.loads(line) for line in
                (ROOT / "runs/rl-review-grpo-pilot-v2/rollouts.jsonl").read_text().splitlines()]
    scores = [row["score"] for row in rollouts if row["group"] == 24]
    assert len(scores) == 4
    assert len({score["reward"] for score in scores}) > 1
    assert all(score["reward"] < 0 and not score["strict_correct"] for score in scores)

    rewards = [binary_training_reward(score) for score in scores]

    assert rewards == [-1.0] * 4
    assert not group_eligible(rewards)


@pytest.fixture(scope="module")
def calibrated_groups():
    dataset = ROOT / "datasets/rl-review-pilot-v2"
    rows = {row["id"]: row for row in map(json.loads, (dataset / "train.jsonl").read_text().splitlines())}
    rollouts = [json.loads(line) for line in
                (ROOT / "runs/rl-review-grpo-pilot-v2/rollouts.jsonl").read_text().splitlines()]
    catalog = load_reward_rules(ROOT / "configs/rl-review-reward-rules-retry.yaml")
    groups = {}
    for group in (6, 26):
        candidates = [row for row in rollouts if row["group"] == group]
        source = rows[candidates[0]["sample_id"]]
        steps = {frame["step"] for frame in source["review_frames"]}
        profile = source["review_profile"]
        gold = validate_gold(json.loads(source["messages"][-1]["content"]), steps, profile)
        rules = rules_for_sample(catalog, source, dataset)
        scores = [score_review(row["raw"], gold, steps, profile, rules=rules,
                               truncated="transport_error" in row["score"])
                  for row in candidates]
        groups[group] = gold["verdict"], scores
    return groups


@pytest.mark.parametrize("group,label,expected", [
    (6, "fail", [-1.0, 1.0, 1.0, 1.0]),
    (26, "blocked", [-1.0, -1.0, 1.0, -1.0]),
])
def test_calibrated_fail_and_blocked_groups_provide_real_training_comparisons(
        calibrated_groups, group, label, expected):
    gold_label, scores = calibrated_groups[group]

    rewards = [binary_training_reward(score) for score in scores]

    assert gold_label == label
    assert rewards == expected
    assert group_eligible(rewards)
    assert not scores[0]["strict_correct"]


def test_correct_verdict_with_reversed_evidence_is_still_negative(calibrated_groups):
    _, scores = calibrated_groups[6]
    reversed_stages = scores[0]
    assert reversed_stages["verdict_correct"]
    assert any("citation.invalid:" in error for error in reversed_stages["errors"])
    assert binary_training_reward(reversed_stages) == -1.0


@pytest.mark.parametrize("eligibility", [(False, True), (False, False)])
def test_resampling_selects_a_whole_group_and_stops_at_the_attempt_limit(monkeypatch, tmp_path, eligibility):
    class FakeBase:
        def __init__(self):
            self.generated = []
            self.processing_class = object()

        def _generate_single_turn(self, prompts):
            attempt = len(self.generated) + 1
            generated = ([list(prompt) for prompt in prompts],
                         [[attempt, candidate] for candidate in range(4)], None, {})
            self.generated.append(generated)
            return generated

    outcomes = iter(eligibility)
    reviewed_groups = []

    def preview(sample_id, completion_ids, processor):
        reviewed_groups.append(completion_ids)
        return {"sample_id": sample_id, "eligible": next(outcomes)}

    monkeypatch.setattr("guiqa.train_review_grpo_pilot.pilot_trainer", lambda: FakeBase)
    reward = SimpleNamespace(output=tmp_path, groups=[], preview=preview)
    trainer = retry_trainer(reward, max_attempts=2)()
    trainer.retry_sample_id = "sample"

    generated = trainer._generate_single_turn([[1], [1], [1], [1]])

    attempts = [json.loads(line) for line in (tmp_path / "sampling-attempts.jsonl").read_text().splitlines()]
    assert generated is trainer.generated[1]
    assert len(trainer.generated) == 2
    assert reviewed_groups == [[[1, candidate] for candidate in range(4)],
                               [[2, candidate] for candidate in range(4)]]
    assert [row["selected"] for row in attempts] == [False, True]
    assert [row["eligible"] for row in attempts] == list(eligibility)
