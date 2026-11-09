"""Organize public browser evidence and read visible fields before judging."""

import json

from .actions import parse_action
from .control import REVIEW_EVIDENCE_FIELDS


PROFILE_RULES = {
    "persistence": (
        "Report pass or fail only after the exact requested input, an accepted submission, "
        "and the same record's reopened value are visibly established. A list row showing "
        "the tested field and identifying the record is sufficient. Use fail for a persisted "
        "value that differs after valid submission; use pass when it matches. A mismatch "
        "after reopening does not invalidate the earlier input evidence. Report blocked "
        "when input, submission, or the final record is unestablished, or the last edit was "
        "not submitted."
    ),
    "business_outcome_v1": (
        "For an accepted save, require the exact requested input, accepted submission and "
        "the same reopened record; compare its value with the task. A task-specific business "
        "rejection of correctly entered valid input is fail. If the task explicitly requires "
        "rejecting invalid or duplicate input, that intended rejection is pass, and actually "
        "saving the invalid record is fail. submission_observed means the submit action "
        "completed and its result is visible; business_rejection_visible means the relevant "
        "business error is visible; expected_rejection comes from the public task. A rejected "
        "save has submission_succeeded=false. Those false persistence fields do not turn a "
        "demonstrated business rejection into blocked. Report blocked when the exact input, "
        "submit outcome or task-specific result is unestablished. A rejection response that "
        "visibly retains the submitted form fields can establish input_value at that response "
        "step. Distinguish that rejected form from an old persisted value after an accepted save."
    ),
}


def select_evidence(history, differences, before, after):
    edits = [row for row in history if row["step"] <= before and is_edit(row.get("action"))]
    # Tiny fields can change less than the typing-feedback threshold; retain any visible edit.
    changed = [row for row in edits if differences.get(row["step"], 0) > 0]
    selected = []
    if edits:
        typed = (changed or edits)[-1]
        selected.append((typed["step"], "input_candidate" if changed else "input_attempt"))
        post_actions = [row for row in history if typed["step"] < row["step"] <= before
                        and is_post_edit_action(row.get("action"))]
        reload_step = next((row["step"] for row in history if typed["step"] < row["step"] <= before
                            and row.get("action") and row["action"]["action"] == "press"
                            and row["action"].get("key") in ("Control+r", "F5")), None)
        # A modal save can precede the actual record save; keep the action before its reload.
        if reload_step is not None:
            post = next((row for row in reversed(post_actions) if row["step"] < reload_step), None)
        else:
            post = next(iter(post_actions), None)
        if post:
            selected.append((post["step"], "post_edit_action"))
    selected.append((before, "outcome_candidate"))
    if after > before:
        action = next(row.get("action") for row in history if row["step"] == after and "action" in row)
        reloaded = action and action["action"] == "press" and action.get("key") in ("Control+r", "F5")
        selected.append((after, "after_reload" if reloaded else "record_view_candidate"))
    frames = {}
    for step, role in selected:
        frames.setdefault(step, {"step": step, "roles": []})["roles"].append(role)
    return list(frames.values())


def review_content(task, history, frames, paths):
    steps = [frame["step"] for frame in frames]
    start, end = max(0, min(steps) - 2), max(steps)
    action_window = [row for row in history if start <= row["step"] <= end]
    content = [{"type": "text", "text": task
                + "\nSupplied screenshot steps: " + json.dumps(steps)
                + f"\nExecuted actions from step {start} through {end}:\n" + json.dumps(action_window)
                + "\nEarlier actions are omitted. Omitted actions cannot be cited as screenshot "
                  "evidence; use only the supplied screenshot steps."
                + "\nEvidence roles describe candidate stages, not established facts. "
                  "A visible change does not prove exact input, and a post-edit action does "
                  "not prove submission. Read the screenshots to establish each fact."}]
    actions = {row["step"]: row.get("action") for row in action_window if "action" in row}
    for frame in frames:
        step = frame["step"]
        text = (f"Screenshot step {step}; candidate roles: {', '.join(frame['roles'])}; "
                f"action at this step: {json.dumps(actions.get(step), ensure_ascii=False)}")
        content.extend([{"type": "text", "text": text}, {"type": "image", "image": paths[step]}])
    return content


def review_prompt(profile="persistence"):
    example = {
        "action": "finish",
        "field_readings": [{"field": "field label", "requested_value": "value from the task",
                            "input_value": "literal value visible before submit", "input_step": 3,
                            "observed_value": "literal value visible after reopen", "observed_step": 7}],
        "submission_step": 4,
        "requested_value_entered": True, "submission_succeeded": True, "final_record_visible": True,
    }
    if profile == "business_outcome_v1":
        example.update(submission_observed=True, business_rejection_visible=False, expected_rejection=False)
    example.update(verdict="pass|fail|blocked", observation="submission outcome, record identity and comparison",
                   evidence_steps=[3, 4, 7])
    return ("You judge a browser form test from screenshots and executed actions. First transcribe "
            "the task's fields and the visible values, then establish submission and compare the result.\n"
            "Screenshot roles are candidates selected from actions and visual changes, not "
            "proof of saved values or successful submission.\n"
            "For each tested field, return its field label, requested_value from the public task, "
            "input_value copied from the input screenshot, and observed_value copied from the final "
            "same-record screenshot, with their screenshot steps. Preserve exact characters, signs, "
            "separators, units and decimal digits. Use null for an unreadable or clipped value and "
            "its step. Never copy the requested value into a reading to fill a gap. A type action "
            "describes attempted input; its text is not a screenshot transcription.\n"
            "For a field the task says to preserve, verify its saved value; no new input is required. "
            "Its input_value may be null when no input was made. requested_value_entered concerns "
            "the actual requested edits or selections.\n"
            "Match the field label and the record identity across frames. Read the tested field, "
            "rather than a neighboring field. For numbers, interpret formatting only from the "
            "task or visible UI; preserve both raw readings and explain the comparison.\n"
            "submission_step cites the screenshot visibly establishing the submit outcome, or null "
            "when it is unestablished. A click, a candidate role or a success toast alone does not "
            "prove that the requested value was entered correctly.\n"
            + PROFILE_RULES[profile] + "\n"
            "Set requested_value_entered, submission_succeeded and final_record_visible from "
            "the visible evidence. Cite only screenshots supplied for this review. Return one "
            "JSON object with field_readings before the verdict, without browser actions:\n"
            + json.dumps(example))


def parse_review(raw, selected_steps, profile="persistence"):
    answer = parse_action(raw, set(selected_steps), normalize_reload=True)
    if answer["action"] != "finish":
        raise ValueError("review phase requires finish")
    readings = answer.get("field_readings")
    if not isinstance(readings, list) or not readings:
        raise ValueError("review requires field_readings for the tested fields")
    for reading in readings:
        if not isinstance(reading, dict) or not isinstance(reading.get("field"), str) or not reading["field"].strip():
            raise ValueError("each field reading requires a field label")
        if not isinstance(reading.get("requested_value"), str):
            raise ValueError("each field reading requires the requested value as a string")
        for prefix in ("input", "observed"):
            value, step = reading.get(f"{prefix}_value"), reading.get(f"{prefix}_step")
            if f"{prefix}_value" not in reading or f"{prefix}_step" not in reading:
                raise ValueError(f"each field reading requires {prefix}_value and {prefix}_step")
            if value is None:
                if step is not None:
                    raise ValueError(f"an unreadable {prefix}_value must have a null step")
            elif not isinstance(value, str) or type(step) is not int or step not in selected_steps:
                raise ValueError(f"{prefix}_value must cite a supplied screenshot step")
    if "submission_step" not in answer:
        raise ValueError("review requires submission_step or null")
    submission = answer["submission_step"]
    if submission is not None and (type(submission) is not int or submission not in selected_steps):
        raise ValueError("submission_step must cite a supplied screenshot or be null")
    fields = REVIEW_EVIDENCE_FIELDS
    if profile == "business_outcome_v1":
        fields += ("submission_observed", "business_rejection_visible", "expected_rejection")
    if any(type(answer.get(field)) is not bool for field in fields):
        raise ValueError("review evidence fields must be boolean")
    return answer


def is_edit(action):
    return action and (action["action"] == "type" or
                       action["action"] == "press" and action.get("key") in ("Backspace", "Delete"))


def is_post_edit_action(action):
    return action and (action["action"] == "click" or
                       action["action"] == "press" and action.get("key") == "Enter")
