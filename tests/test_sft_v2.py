from pathlib import Path

from guiqa.sft_v2 import bounded_review, final_input_steps, read_json, read_rows, review_selection


ROOT = Path(__file__).resolve().parents[1]


def test_review_window_uses_adjacent_real_reopen_frames():
    directory = ROOT / "runs/20261107T160418Z-4d4606ef"
    trace = read_rows(directory / "trace.jsonl")
    events = read_json(directory / "private/oracle.json")["events"]
    selection = review_selection(trace, events, "persistence")
    assert selection["before_step"] == 15
    assert selection["after_step"] == 16
    assert selection["screenshot_steps"] == [8, 9, 15, 16]
    assert selection["review_alignment"] == "actual_record_reopen"


def test_later_login_replaces_controller_last_type():
    directory = ROOT / "runs/20261107T094135Z-7c87c261"
    trace = read_rows(directory / "trace.jsonl")
    events = read_json(directory / "private/oracle.json")["events"]
    selection = review_selection(trace, events, "persistence")
    assert selection["before_step"] == 14
    assert selection["after_step"] == 15
    assert selection["last_type_step"] == 14
    assert 3 not in selection["screenshot_steps"]


def test_only_final_input_state_per_focus_is_required():
    actions = [None, {"action": "click"}, {"action": "press", "key": "Backspace"},
               {"action": "press", "key": "Backspace"}, {"action": "type", "text": "Exact"},
               {"action": "click"}, {"action": "press", "key": "Delete"}, {"action": "click"}]
    trace = [{"step": step, "action": action} for step, action in enumerate(actions)]
    assert final_input_steps(trace, [{"kind": "submit_response", "step": 7}], 7) == [4, 6]


def test_manual_same_frame_field_proof_restores_without_adding_images():
    directory = ROOT / "runs/20261107T160418Z-4d4606ef"
    trace = read_rows(directory / "trace.jsonl")
    events = read_json(directory / "private/oracle.json")["events"]
    original = read_rows(directory / "sft/samples.jsonl")[-1]["assistant"]
    selection = review_selection(trace, events, "persistence")
    blocked, _ = bounded_review(original, trace, events, selection, directory)
    assert blocked["verdict"] == "blocked" and not blocked["requested_value_entered"]
    proof = {"frames": [{"step": 8, "input_proof_steps": [5, 8],
                          "observation": "Blue Quantity 123 and Weight 1.25 are both completely visible."}]}
    reviewed, basis = bounded_review(original, trace, events, selection, directory, proof)
    assert reviewed["verdict"] == "fail" and reviewed["requested_value_entered"]
    assert reviewed["evidence_steps"] == selection["screenshot_steps"]
    assert not basis["missing_input_proof_steps"]


def test_clipped_input_is_not_proved_by_merely_including_its_step():
    directory = ROOT / "runs/20261107T161515Z-b2c97ecb"
    trace = read_rows(directory / "trace.jsonl")
    events = read_json(directory / "private/oracle.json")["events"]
    original = read_rows(directory / "sft/samples.jsonl")[-1]["assistant"]
    selection = review_selection(trace, events, "business_outcome_v1")
    review, _ = bounded_review(original, trace, events, selection, directory,
                               {"unestablished_input_proof_steps": [5]})
    assert review["verdict"] == "blocked"
    assert not review["requested_value_entered"]
    assert review["business_rejection_visible"]
    assert not review["submission_succeeded"] and not review["final_record_visible"]
