from guiqa.train_review_grpo import resolve_training_reward, trusted_group_rewards


def test_untrusted_candidate_drops_the_whole_task_reward_group():
    scores = [{"reward": -0.8}, {"reward": None, "provisional_reward": 1.2},
              {"reward": -1.0}, {"reward": -0.9}]
    assert trusted_group_rewards(scores) == ([0.0, 0.0, 0.0, 0.0], True)


def test_trusted_numeric_rewards_keep_their_order_and_values():
    scores = [{"reward": value} for value in (-1.0, -0.8, 1.2, -0.9)]
    assert trusted_group_rewards(scores) == ([-1.0, -0.8, 1.2, -0.9], False)


class NoProvisional(dict):
    def __getitem__(self, key):
        if key == "provisional_reward":
            raise AssertionError("training must not read provisional_reward")
        return super().__getitem__(key)


def test_known_contradiction_with_unverified_citation_is_certain_negative():
    score = NoProvisional(reward=None, provisional_reward=1.2,
                          errors=["expected_rejection:mismatch"],
                          unverified_citations=[{"path": "result_steps", "claimed_steps": [6]}])
    resolved = resolve_training_reward(score)
    assert resolved == {"reward": -1.0, "certain_negative": True,
                        "proof_paths": ["expected_rejection:mismatch"]}
    assert trusted_group_rewards([{"reward": -0.8}, resolved, {"reward": 1.2}, {"reward": -0.9}]) == (
        [-0.8, -1.0, 1.2, -0.9], False)


def test_pure_alternate_citation_still_masks_the_complete_group():
    score = NoProvisional(reward=None, provisional_reward=1.2, errors=[],
                          unverified_citations=[{"path": "field_readings.Title.before.step", "claimed_steps": [4]}])
    resolved = resolve_training_reward(score)
    assert resolved["reward"] is None and resolved["proof_paths"] == []
    assert trusted_group_rewards([{"reward": -0.8}, resolved, {"reward": -1.0}, {"reward": -0.9}]) == (
        [0.0, 0.0, 0.0, 0.0], True)


def test_requested_text_or_missing_evidence_is_not_a_certain_negative_proof():
    score = NoProvisional(reward=None, provisional_reward=-0.8,
                          errors=["field_readings.Title.requested_value:mismatch",
                                  "guard:review_evidence_not_established:submission"])
    assert resolve_training_reward(score) == {"reward": None, "certain_negative": False, "proof_paths": []}
