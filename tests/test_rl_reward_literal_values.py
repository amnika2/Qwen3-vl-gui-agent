import copy
import json

from guiqa.rl_review_reward import score_review


def test_verified_compound_control_value_keeps_its_original_stage_requirement():
    gold = {
        "action": "finish", "task_kind": "edit",
        "field_readings": [{"field": "Weight", "intent": "edit", "requested_value": "0.35",
                            "before": {"value": "0.35", "step": 6},
                            "after": {"value": "0.35", "step": 14},
                            "input_matches_request": True, "result_matches_request": True}],
        "submission": {"status": "accepted", "evidence": {"step": 9, "observation": "Saved"}},
        "result_observed": True, "result_steps": [14], "expected_rejection": False,
        "verdict": "pass", "observation": "Weight persisted.", "evidence_steps": [6, 9, 14],
    }
    answer = copy.deepcopy(gold)
    answer["field_readings"][0]["before"]["value"] = "+0.35"
    rules = {"approved_values": {"field_readings.Weight.before.value": ["+0.35"]},
             "invalid_citations": {"field_readings.Weight.before.step": [14]}}
    scored = score_review(json.dumps(answer), gold, {6, 9, 14}, rules=rules)
    assert scored["strict_correct"] and scored["reward"] == 1.2
    assert not score_review(json.dumps(answer), gold, {6, 9, 14})["strict_correct"]
    answer["field_readings"][0]["before"]["step"] = 14
    wrong_stage = score_review(json.dumps(answer), gold, {6, 9, 14}, rules=rules)
    assert wrong_stage["reward"] < 0 and not wrong_stage["strict_correct"]
    answer["field_readings"][0]["before"] = {"value": "+20", "step": 6}
    wrong_value = score_review(json.dumps(answer), gold, {6, 9, 14}, rules=rules)
    assert wrong_value["reward"] < 0 and not wrong_value["strict_correct"]
