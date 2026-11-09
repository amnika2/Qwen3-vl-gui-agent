import json
from pathlib import Path

import pytest

from guiqa.control import LoopControl
from guiqa.review_evidence import parse_review, review_content, review_prompt, select_evidence


def test_effective_input_is_kept_after_unfocused_typing():
    history = [
        {"step": 0, "action": None, "result": "executed"},
        {"step": 1, "action": {"action": "type", "text": "1.234.567,89"}, "result": "executed"},
        {"step": 2, "action": {"action": "click", "coordinate": [500, 800]}, "result": "executed"},
        {"step": 3, "action": {"action": "click", "coordinate": [500, 400]}, "result": "executed"},
        {"step": 4, "action": {"action": "type", "text": "1.234.567,89"}, "result": "executed; no visible change"},
        {"step": 5, "action": {"action": "press", "key": "Control+r"}, "result": "executed"},
    ]
    frames = select_evidence(history, {1: 0.12, 4: 0.0}, before=4, after=5)
    assert frames == [{"step": 1, "roles": ["input_candidate"]},
                      {"step": 2, "roles": ["post_edit_action"]},
                      {"step": 4, "roles": ["outcome_candidate"]},
                      {"step": 5, "roles": ["after_reload"]}]


def test_recorded_small_amount_edits_survive_later_unfocused_typing():
    directory = Path(__file__).resolve().parents[1] / "runs/20261107T182351Z-05f11903"
    trace = [json.loads(line) for line in (directory / "trace.jsonl").read_text().splitlines()]
    result = json.loads((directory / "result.json").read_text())
    before, after = result["intervention_step"], result["actions"]
    control = LoopControl(directory / "screenshots/000.png")
    differences = {}
    for row in trace[1:before + 1]:
        control.record(row["action"], row["step"], directory / row["screenshot"])
        differences[row["step"]] = control.difference
    history = [{"step": row["step"], "action": row["action"],
                "result": row["execution_error"] or "executed"} for row in trace]
    assert 0 < differences[7] < 0.03 and 0 < differences[10] < 0.03
    assert differences[17] == differences[22] == differences[27] == 0
    assert [frame["step"] for frame in select_evidence(history, differences, before, after)] == [10, 11, before, after]


def test_unsubmitted_prefix_has_no_invented_submission_role():
    history = [{"step": 1, "action": {"action": "type", "text": "New"}, "result": "executed"},
               {"step": 2, "action": {"action": "press", "key": "Control+r"}, "result": "executed"}]
    frames = select_evidence(history, {1: 0.1}, before=1, after=2)
    assert frames == [{"step": 1, "roles": ["input_candidate", "outcome_candidate"]},
                      {"step": 2, "roles": ["after_reload"]}]
    content = review_content("Rename the record", history, frames, {1: "input.png", 2: "reload.png"})
    assert "does not prove submission" in content[0]["text"]
    assert "candidate roles: input_candidate, outcome_candidate" in content[1]["text"]


def test_multistage_save_retains_record_save_before_actual_reload():
    from pathlib import Path

    from guiqa.sft_review_evidence import replay_frames
    from guiqa.sft_v2 import read_rows, trace_history

    directory = Path(__file__).resolve().parents[1] / "runs/20261107T160603Z-c8477069"
    history = trace_history(read_rows(directory / "trace.jsonl"))
    frames = replay_frames(directory, history, before=16, after=17)
    assert [frame["step"] for frame in frames] == [9, 13, 16, 17]
    assert frames[1]["roles"] == ["post_edit_action"]


def test_no_visible_input_retains_attempt_and_no_reload_is_invented():
    history = [{"step": 0, "action": None, "result": "executed"},
               {"step": 1, "action": {"action": "type", "text": "Unfocused"}, "result": "executed"},
               {"step": 2, "action": {"action": "press", "key": "Enter"}, "result": "executed"}]
    assert select_evidence(history, {1: 0}, 2, 2) == [
        {"step": 1, "roles": ["input_attempt"]},
        {"step": 2, "roles": ["post_edit_action", "outcome_candidate"]},
    ]


def test_record_reopen_click_is_not_labelled_as_reload():
    history = [{"step": 1, "action": {"action": "type", "text": "New"}, "result": "executed"},
               {"step": 2, "action": {"action": "click", "coordinate": [500, 800]}, "result": "executed"},
               {"step": 3, "action": {"action": "click", "coordinate": [500, 400]}, "result": "executed"}]
    frames = select_evidence(history, {1: 0.1}, before=2, after=3)
    assert frames[-1] == {"step": 3, "roles": ["record_view_candidate"]}


def test_review_action_window_excludes_earlier_editing_stage():
    history = [
        {"step": 4, "action": {"action": "type", "text": "Shoremark Edited"}, "result": "executed"},
        {"step": 8, "action": {"action": "click", "coordinate": [500, 400]}, "result": "executed"},
        {"step": 9, "action": {"action": "press", "key": "Control+a"}, "result": "executed"},
        {"step": 10, "action": {"action": "type", "text": "Shoremark Final"}, "result": "executed"},
        {"step": 11, "action": {"action": "click", "coordinate": [500, 800]}, "result": "executed"},
        {"step": 12, "action": {"action": "press", "key": "Control+r"}, "result": "executed"},
    ]
    frames = [{"step": 10, "roles": ["input_candidate"]},
              {"step": 11, "roles": ["post_edit_action", "outcome_candidate"]},
              {"step": 12, "roles": ["after_reload"]}]
    content = review_content("Set Label to Shoremark Final.", history, frames,
                             {10: "10.png", 11: "11.png", 12: "12.png"})
    text = content[0]["text"]
    assert "Supplied screenshot steps: [10, 11, 12]" in text
    assert "Executed actions from step 8 through 12" in text
    assert "Shoremark Edited" not in text
    assert '"step": 8' in text and '"key": "Control+a"' in text
    assert '"step": 10' in text and '"text": "Shoremark Final"' in text
    assert '"step": 11' in text and '"step": 12' in text
    assert "Omitted actions cannot be cited as screenshot evidence" in text
    assert [item["image"] for item in content if item["type"] == "image"] == ["10.png", "11.png", "12.png"]


def test_review_preserves_raw_amounts_and_allows_unreadable_values():
    answer = {"action": "finish", "field_readings": [
        {"field": "Sale price", "requested_value": "1.234.567,89", "input_value": "1.234.567,89",
         "input_step": 3, "observed_value": "$1,234.57", "observed_step": 7},
        {"field": "Purchase price", "requested_value": "100", "input_value": None,
         "input_step": None, "observed_value": None, "observed_step": None}],
        "submission_step": 4, "requested_value_entered": False, "submission_succeeded": True,
        "final_record_visible": True, "verdict": "blocked", "observation": "Purchase price is offscreen",
        "evidence_steps": [3, 4, 7]}
    assert parse_review(json.dumps(answer), {3, 4, 7}) == answer
    assert "Preserve exact characters" in review_prompt()
    assert "expected_rejection" in review_prompt("business_outcome_v1")


@pytest.mark.parametrize("mutation", [
    lambda answer: answer["field_readings"][0].update(input_step=99),
    lambda answer: answer["field_readings"][0].update(observed_value=None),
    lambda answer: answer["field_readings"][0].update(input_value=123),
    lambda answer: answer.update(submission_step=99),
    lambda answer: answer.update(evidence_steps=[99]),
])
def test_review_references_only_supplied_images(mutation):
    answer = {"action": "finish", "field_readings": [
        {"field": "Name", "requested_value": "New", "input_value": "New", "input_step": 3,
         "observed_value": "Old", "observed_step": 7}],
        "submission_step": 4, "requested_value_entered": True, "submission_succeeded": True,
        "final_record_visible": True, "verdict": "fail", "observation": "Old name persisted",
        "evidence_steps": [3, 4, 7]}
    mutation(answer)
    with pytest.raises(ValueError):
        parse_review(json.dumps(answer), {3, 4, 7})
