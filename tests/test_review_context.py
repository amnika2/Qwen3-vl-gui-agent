import json
from pathlib import Path

from guiqa.review_context import review_content, select_evidence


def row(step, action=None):
    return {"step": step, "action": action, "result": "executed"}


def test_edit_retains_visible_baseline_input_response_and_final():
    history = [row(0), row(1, {"action": "click", "coordinate": [100, 100]}),
               row(2, {"action": "press", "key": "Control+a"}),
               row(3, {"action": "type", "text": "New"}),
               row(4, {"action": "click", "coordinate": [100, 200]}),
               row(5, {"action": "press", "key": "Control+r"}),
               row(6, {"action": "press", "key": "Control+r"})]
    assert select_evidence(history, {3: 0.1}, 5, 6) == [
        {"step": 2, "roles": ["before_input_candidate"]},
        {"step": 3, "roles": ["input_candidate"]},
        {"step": 4, "roles": ["submission_response_candidate"]},
        {"step": 5, "roles": ["outcome_candidate"]},
        {"step": 6, "roles": ["after_reload"]},
    ]


def test_clear_field_is_an_edit_and_keeps_empty_field_frame():
    history = [row(0), row(1, {"action": "press", "key": "Control+a"}),
               row(2, {"action": "press", "key": "Backspace"}),
               row(3, {"action": "click", "coordinate": [100, 200]}),
               row(4, {"action": "press", "key": "Control+r"})]
    frames = select_evidence(history, {2: 0.01}, 3, 4)
    assert [frame["step"] for frame in frames] == [1, 2, 3, 4]
    assert frames[1]["roles"] == ["input_candidate"]
    assert frames[2]["roles"] == ["submission_response_candidate", "outcome_candidate"]


def test_multiple_edits_use_baseline_before_first_edit_and_keep_distinct_final_views():
    history = [row(0), row(1, {"action": "type", "text": "Subject"}),
               row(2, {"action": "click", "coordinate": [100, 100]}),
               row(3, {"action": "type", "text": "Content"}),
               row(4, {"action": "click", "coordinate": [100, 200]}),
               row(5, {"action": "press", "key": "Control+r"}),
               row(6, {"action": "scroll", "pixels": 500}),
               row(7, {"action": "scroll", "pixels": 500})]
    frames = select_evidence(history, {1: 0.1, 3: 0.2}, 6, 7)
    assert [frame["step"] for frame in frames] == [0, 3, 4, 6, 7]
    assert len(frames) == 5


def test_initial_baseline_nomination_is_kept_before_scrolling_to_edit():
    history = [row(0), row(1, {"action": "scroll", "pixels": 500}),
               row(2, {"action": "press", "key": "Control+a"}),
               row(3, {"action": "type", "text": "Content"}),
               row(4, {"action": "click", "coordinate": [100, 200]}),
               row(5, {"action": "press", "key": "Control+r"})]
    frames = select_evidence(history, {3: 0.2}, 4, 5, [99, 2, 0, 4, 5])
    assert [frame["step"] for frame in frames] == [0, 3, 4, 5]
    assert frames[0]["roles"] == ["before_input_candidate"]
    default = select_evidence(history, {3: 0.2}, 4, 5, [99, 4, 5])
    assert [frame["step"] for frame in default] == [2, 3, 4, 5]


def test_creation_without_typing_uses_intermediate_public_nomination():
    history = [row(0), row(1, {"action": "click", "coordinate": [100, 100]}),
               row(2, {"action": "click", "coordinate": [100, 200]}),
               row(3, {"action": "click", "coordinate": [100, 300]}),
               row(4, {"action": "press", "key": "Control+r"}),
               row(5, {"action": "press", "key": "Control+r"})]
    frames = select_evidence(history, {1: 5, 2: 10, 3: 3}, 4, 5, [99, 2, 4])
    assert [frame["step"] for frame in frames] == [1, 2, 4, 5]
    assert frames[0]["roles"] == ["before_trigger_candidate"]
    assert frames[1]["roles"] == ["trigger_response_candidate"]


def test_terminal_nomination_does_not_displace_earlier_creation_response():
    history = [row(0), row(1, {"action": "click", "coordinate": [100, 100]}),
               row(2, {"action": "click", "coordinate": [100, 200]}),
               row(3, {"action": "press", "key": "Enter"}),
               row(4, {"action": "press", "key": "Control+r"})]
    assert [frame["step"] for frame in select_evidence(history, {1: 5, 2: 3, 3: 0.2}, 3, 4, [3])] == [0, 1, 3, 4]


def test_recorded_no_type_creation_retains_confirmation_and_public_actions():
    directory = Path(__file__).resolve().parent / "fixtures/no-type-creation"
    trace = [json.loads(line) for line in (directory / "trace.jsonl").read_text().splitlines()]
    history = [row(item["step"], item["action"]) for item in trace]
    frames = select_evidence(history, {1: 4.0, 2: 2.0, 3: 0.0, 6: 3.0}, 6, 7, [6])
    assert [frame["step"] for frame in frames] == [0, 1, 6, 7]
    content = review_content("Create an automatically named record.", history, frames,
                             {frame["step"]: str(directory / f"screenshots/{frame['step']:03d}.png")
                              for frame in frames})
    assert '"step": 1' in content[0]["text"]
    assert '"step": 5' in content[0]["text"]
    assert "first changing click may be navigation" in content[0]["text"]
    assert "empty string \"\"" in content[0]["text"]
    assert "AFTER this executed action" in content[1]["text"]
    assert len([part for part in content if part["type"] == "image"]) == 4


def test_unsubmitted_input_does_not_invent_response_and_stays_within_actual_steps():
    history = [row(1, {"action": "type", "text": "Unfocused"}),
               row(2, {"action": "press", "key": "Control+r"})]
    assert select_evidence(history, {1: 0}, 1, 2, [99]) == [
        {"step": 1, "roles": ["input_attempt", "outcome_candidate"]},
        {"step": 2, "roles": ["after_reload"]},
    ]
