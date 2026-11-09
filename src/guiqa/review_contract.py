"""Intent-aware review of public screenshot evidence."""

import json


MAX_OBSERVATION_LENGTH = 512


PROFILE_RULES = {
    "persistence": (
        "An accepted submission with matching visible results is pass; a visible saved "
        "result that differs from the task is fail. A rejected or unknown submission is "
        "blocked. A saved mismatch does not invalidate correctly entered input or an "
        "accepted submission."
    ),
    "business_outcome_v1": (
        "A visible task-specific rejection of correctly entered input is pass when the "
        "public task expects rejection, and fail otherwise. Rejected submissions do not "
        "require a saved record. An accepted submission that saves input the task says "
        "must be rejected is fail. For other accepted submissions, compare the visible "
        "saved result with the task. Set expected_rejection only from the public task."
    ),
}

TOP_FIELDS = {
    "action", "task_kind", "field_readings", "submission", "result_observed",
    "result_steps", "expected_rejection", "verdict", "observation", "evidence_steps",
}
READING_FIELDS = {
    "field", "intent", "requested_value", "before", "after",
    "input_matches_request", "result_matches_request",
}


def review_prompt(profile="persistence"):
    example = {
        "action": "finish", "task_kind": "edit",
        "field_readings": [{
            "field": "field label", "intent": "edit", "requested_value": "task value",
            "before": {"value": "literal entered value", "step": 3},
            "after": {"value": "literal saved value", "step": 7},
            "input_matches_request": True, "result_matches_request": True,
        }],
        "submission": {"status": "accepted", "evidence": {
            "step": 4, "observation": "visible submission outcome and record identity"}},
        "result_observed": True, "result_steps": [7], "expected_rejection": False,
        "verdict": "pass", "observation": "submission outcome and field comparison",
        "evidence_steps": [3, 4, 7],
    }
    return (
        "Judge the public browser task using the supplied screenshots and executed actions. "
        "Return exactly one finish JSON object in evidence_v2 format. First read each field "
        "under its own visible label and verify the same record across screenshots. Read "
        "the field requested by the task, not a neighboring field. Preserve exact characters, "
        "signs, separators, units and decimal digits. Interpret formatting only from the "
        "task or visible UI and explain numeric comparisons in observation.\n"
        "Use one field_readings item per field, including preservation constraints. Set "
        "intent=edit for a requested input or selection, preserve for a field that must "
        "remain unchanged, and generated for a value created by the application. task_kind "
        "is create when the task creates a record, otherwise edit. requested_value comes "
        "from the public task. For preserve, use the exact requested baseline if the task "
        "gives one; otherwise use the literal string 'unchanged'. For generated, describe "
        "the required naming rule or prefix from the task.\n"
        "before and after are atomic null or {value:string, step:integer} readings. "
        "Use value='' for a visibly empty field; null means unreadable, absent or unseen, "
        "never a visible empty value. before is the visible entered value before submission "
        "for edit, and the original baseline for preserve. after is the visible final value "
        "of the same record. Do not copy task text or a type action into a screenshot reading. "
        "For preserve with requested_value='unchanged', obtain before as the baseline.\n"
        "Set input_matches_request from the visible before reading for edit only. "
        "Set it to null for preserve and generated; these intents require no new typing. "
        "Set result_matches_request from after and the task, or the before baseline for "
        "an unchanged field. Use null if comparison is unestablished. A result screenshot "
        "or a nonempty step list does not itself establish a match.\n"
        "submission.status is accepted, rejected or unknown. Cite the screenshot and "
        "describe its visible outcome in submission.evidence, or use null if unestablished. "
        "A click or a candidate screenshot role alone cannot establish acceptance. A modal "
        "closing may establish acceptance when the visible context identifies the completed "
        "save; compare its record after reopen to judge persistence separately. Do not "
        "change accepted to unknown just because the saved field is wrong.\n"
        "For create/generated flows, inspect the screenshot before creation, the creation "
        "outcome, and the identified new record after creation. The absence of typing is "
        "expected for automatic naming. A pre-existing row alone does not prove creation. "
        "Use result_observed only when the requested record/result is visibly established, "
        "and list its actual screenshot steps in result_steps. A list row that identifies "
        "the record and shows the tested field is sufficient.\n"
        "For an accepted pass/fail, establish every requested edit's correct visible input, "
        "the accepted submission and all compared final fields. Use blocked when the "
        "necessary evidence is unestablished. Cite only supplied screenshot steps; include "
        "the before-creation and outcome frames in evidence_steps for creation. "
        + PROFILE_RULES[profile] + "\nReturn this structure, using real readings and references:\n"
        + json.dumps(example)
    )


def json_schema(selected_steps):
    step = {"type": "integer", "enum": sorted(set(selected_steps))}
    reading = {"anyOf": [
        {"type": "null"},
        _object_schema({"value": {"type": "string"}, "step": step}),
    ]}
    nullable_bool = {"anyOf": [{"type": "boolean"}, {"type": "null"}]}
    steps = {"type": "array", "items": step}
    return _object_schema({
        "action": {"type": "string", "enum": ["finish"]},
        "task_kind": {"type": "string", "enum": ["edit", "create"]},
        "field_readings": {"type": "array", "minItems": 1, "items": _object_schema({
            "field": {"type": "string", "minLength": 1},
            "intent": {"type": "string", "enum": ["edit", "preserve", "generated"]},
            "requested_value": {"type": "string"}, "before": reading, "after": reading,
            "input_matches_request": nullable_bool, "result_matches_request": nullable_bool,
        })},
        "submission": _object_schema({
            "status": {"type": "string", "enum": ["accepted", "rejected", "unknown"]},
            "evidence": {"anyOf": [
                {"type": "null"},
                _object_schema({"step": step, "observation": {
                    "type": "string", "minLength": 1, "maxLength": MAX_OBSERVATION_LENGTH}}),
            ]},
        }),
        "result_observed": {"type": "boolean"}, "result_steps": steps,
        "expected_rejection": {"type": "boolean"},
        "verdict": {"type": "string", "enum": ["pass", "fail", "blocked"]},
        "observation": {"type": "string", "minLength": 1, "maxLength": MAX_OBSERVATION_LENGTH},
        "evidence_steps": {**steps, "minItems": 1},
    })


def parse_review(raw, selected_steps, profile="persistence"):
    text = raw.strip()
    if text.startswith("<tool_call>") and text.endswith("</tool_call>"):
        text = text[len("<tool_call>"):-len("</tool_call>")].strip()
    if text.startswith("```json\n") and text.endswith("```"):
        text = text[8:-3].strip()
    answer = json.loads(text)
    _object(answer, TOP_FIELDS, "review")
    if profile not in PROFILE_RULES:
        raise ValueError("unsupported review profile")
    if answer["action"] != "finish" or answer["task_kind"] not in ("edit", "create"):
        raise ValueError("review requires finish and task_kind edit or create")
    if answer["verdict"] not in ("pass", "fail", "blocked"):
        raise ValueError("verdict must be pass, fail or blocked")
    _string(answer["observation"], "observation", nonempty=True, max_length=MAX_OBSERVATION_LENGTH)
    for key in ("result_observed", "expected_rejection"):
        if type(answer[key]) is not bool:
            raise ValueError(f"{key} must be boolean")
    _steps(answer["evidence_steps"], selected_steps, "evidence_steps", nonempty=True)
    _steps(answer["result_steps"], selected_steps, "result_steps")
    readings = answer["field_readings"]
    if not isinstance(readings, list) or not readings:
        raise ValueError("field_readings must contain at least one field")
    fields = set()
    for reading in readings:
        _object(reading, READING_FIELDS, "field reading")
        _string(reading["field"], "field", nonempty=True)
        field = reading["field"].strip().casefold()
        if field in fields:
            raise ValueError("each field must have exactly one reading")
        fields.add(field)
        if reading["intent"] not in ("edit", "preserve", "generated"):
            raise ValueError("field intent must be edit, preserve or generated")
        _string(reading["requested_value"], "requested_value")
        for key in ("before", "after"):
            _field_value(reading[key], selected_steps, key)
        for key in ("input_matches_request", "result_matches_request"):
            if reading[key] is not None and type(reading[key]) is not bool:
                raise ValueError(f"{key} must be boolean or null")
    submission = answer["submission"]
    _object(submission, {"status", "evidence"}, "submission")
    if submission["status"] not in ("accepted", "rejected", "unknown"):
        raise ValueError("submission status must be accepted, rejected or unknown")
    if submission["evidence"] is not None:
        evidence = submission["evidence"]
        _object(evidence, {"step", "observation"}, "submission evidence")
        _steps([evidence["step"]], selected_steps, "submission evidence")
        _string(evidence["observation"], "submission observation", nonempty=True,
                max_length=MAX_OBSERVATION_LENGTH)
    return answer


def review_guard(answer, profile="persistence"):
    if answer["verdict"] == "blocked":
        return None
    missing = []
    for reading in answer["field_readings"]:
        if reading["intent"] == "edit" and (
            reading["before"] is None or reading["input_matches_request"] is not True
        ):
            missing.append("input:" + reading["field"])
    submission = answer["submission"]
    if submission["evidence"] is None or submission["status"] == "unknown":
        missing.append("submission")
    if missing:
        return "review_evidence_not_established:" + ",".join(missing)
    if profile == "business_outcome_v1" and submission["status"] == "rejected":
        expected = "pass" if answer["expected_rejection"] else "fail"
        return None if answer["verdict"] == expected else "review_rejection_verdict_contradicts_task"
    if submission["status"] != "accepted":
        return "review_evidence_not_established:accepted_submission"
    if not answer["result_observed"] or not answer["result_steps"]:
        missing.append("result")
    for reading in answer["field_readings"]:
        if (reading["intent"] == "preserve" and reading["requested_value"] == "unchanged"
                and reading["before"] is None):
            missing.append("baseline:" + reading["field"])
        if reading["after"] is None or reading["result_matches_request"] is None:
            missing.append("result:" + reading["field"])
    if missing:
        return "review_evidence_not_established:" + ",".join(missing)
    expected = "pass" if all(row["result_matches_request"] for row in answer["field_readings"]) else "fail"
    if profile == "business_outcome_v1" and answer["expected_rejection"]:
        expected = "fail"
    return None if answer["verdict"] == expected else "review_result_verdict_contradicts_fields"


def _object_schema(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def _object(value, fields, name):
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{name} requires exactly: " + ", ".join(sorted(fields)))


def _string(value, name, nonempty=False, max_length=None):
    if not isinstance(value, str) or nonempty and not value.strip():
        raise ValueError(f"{name} must be " + ("a nonempty string" if nonempty else "a string"))
    if max_length is not None and len(value) > max_length:
        raise ValueError(f"{name} must have at most {max_length} characters")


def _steps(value, selected_steps, name, nonempty=False):
    if not isinstance(value, list) or nonempty and not value or any(
        type(step) is not int or step not in selected_steps for step in value
    ):
        raise ValueError(f"{name} must cite supplied screenshot steps")


def _field_value(value, selected_steps, name):
    if value is not None:
        _object(value, {"value", "step"}, name)
        _string(value["value"], name + ".value")
        _steps([value["step"]], selected_steps, name + ".step")
