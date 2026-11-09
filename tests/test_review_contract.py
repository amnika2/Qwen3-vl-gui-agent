import json

import pytest

from guiqa.review_contract import json_schema, parse_review, review_guard, review_prompt


@pytest.fixture
def answer():
    return {
        "action": "finish", "task_kind": "edit",
        "field_readings": [{
            "field": "Nickname", "intent": "edit", "requested_value": "New nickname",
            "before": {"value": "New nickname", "step": 3},
            "after": {"value": "New nickname", "step": 7},
            "input_matches_request": True, "result_matches_request": True,
        }],
        "submission": {"status": "accepted", "evidence": {
            "step": 4, "observation": "Saved record is visible"}},
        "result_observed": True, "result_steps": [7], "expected_rejection": False,
        "verdict": "pass", "observation": "The saved nickname matches",
        "evidence_steps": [3, 4, 7],
    }


def test_preserved_username_does_not_require_input(answer):
    answer["field_readings"].append({
        "field": "Username", "intent": "preserve", "requested_value": "unchanged",
        "before": {"value": "evanRusk", "step": 3},
        "after": {"value": "evanRusk", "step": 7},
        "input_matches_request": None, "result_matches_request": True,
    })
    parsed = parse_review(json.dumps(answer), {3, 4, 7})
    assert review_guard(parsed) is None
    parsed["field_readings"][1]["before"] = None
    assert review_guard(parsed) == "review_evidence_not_established:baseline:Username"
    parsed["field_readings"][1]["requested_value"] = "evanRusk"
    assert review_guard(parsed) is None


def test_visible_empty_mobile_is_a_reading_but_null_is_unknown(answer):
    field = answer["field_readings"][0]
    field.update(field="Mobile", requested_value="+15551234567",
                 before={"value": "+15551234567", "step": 3},
                 after={"value": "", "step": 7}, result_matches_request=False)
    answer["verdict"] = "fail"
    assert review_guard(parse_review(json.dumps(answer), {3, 4, 7})) is None
    field["after"] = None
    assert review_guard(parse_review(json.dumps(answer), {3, 4, 7})) == (
        "review_evidence_not_established:result:Mobile"
    )
    field["after"] = {"value": None, "step": 7}
    with pytest.raises(ValueError, match="after.value"):
        parse_review(json.dumps(answer), {3, 4, 7})


def test_automatic_creation_does_not_require_typing(answer):
    answer["task_kind"] = "create"
    answer["field_readings"] = [{
        "field": "Name", "intent": "generated", "requested_value": "prefix priya-shah",
        "before": None, "after": {"value": "priya-shah-20261108", "step": 7},
        "input_matches_request": None, "result_matches_request": True,
    }]
    assert review_guard(parse_review(json.dumps(answer), {3, 4, 7})) is None
    assert "before creation" in review_prompt()


def test_accepted_submission_can_have_a_failed_saved_value(answer):
    answer["field_readings"][0].update(
        after={"value": "Old nickname", "step": 7}, result_matches_request=False)
    answer["verdict"] = "fail"
    parsed = parse_review(json.dumps(answer), {3, 4, 7})
    assert parsed["submission"]["status"] == "accepted"
    assert review_guard(parsed) is None
    parsed["verdict"] = "pass"
    assert review_guard(parsed) == "review_result_verdict_contradicts_fields"


@pytest.mark.parametrize("mutation", [
    lambda row: row["field_readings"][0]["before"].update(step=99),
    lambda row: row["field_readings"][0]["after"].update(step=99),
    lambda row: row["submission"]["evidence"].update(step=99),
    lambda row: row.update(result_steps=[99]),
    lambda row: row.update(evidence_steps=[99]),
    lambda row: row["field_readings"][0]["before"].update(step=True),
    lambda row: row["field_readings"].append(dict(row["field_readings"][0], field=" nickname ")),
    lambda row: row.update(requested_value_entered=True),
])
def test_review_rejects_invalid_references_duplicates_and_old_fields(answer, mutation):
    mutation(answer)
    with pytest.raises(ValueError):
        parse_review(json.dumps(answer), {3, 4, 7})


@pytest.mark.parametrize("expected,verdict", [(True, "pass"), (False, "fail")])
def test_business_rejection_has_its_own_outcome_without_saved_record(answer, expected, verdict):
    answer.update(expected_rejection=expected, verdict=verdict,
                  result_observed=False, result_steps=[])
    answer["submission"] = {"status": "rejected", "evidence": {
        "step": 4, "observation": "The duplicate nickname is rejected"}}
    answer["field_readings"][0].update(after=None, result_matches_request=None)
    parsed = parse_review(json.dumps(answer), {3, 4, 7}, "business_outcome_v1")
    assert review_guard(parsed, "business_outcome_v1") is None
    assert review_guard(parsed) == "review_evidence_not_established:accepted_submission"
    parsed["verdict"] = "fail" if verdict == "pass" else "pass"
    assert review_guard(parsed, "business_outcome_v1") == "review_rejection_verdict_contradicts_task"


def test_steps_do_not_establish_semantic_input_submission_or_result(answer):
    answer["field_readings"][0]["input_matches_request"] = None
    assert review_guard(answer) == "review_evidence_not_established:input:Nickname"
    answer["field_readings"][0]["input_matches_request"] = True
    answer["submission"]["status"] = "unknown"
    assert review_guard(answer) == "review_evidence_not_established:submission"
    answer["submission"]["status"] = "accepted"
    answer["result_observed"] = False
    assert review_guard(answer) == "review_evidence_not_established:result"


@pytest.mark.parametrize("submission", [False, True])
def test_observation_limit_stops_the_observed_repetition(answer, submission):
    repeated = ("The saved Content differs from the requested edit only in the preserved prefix; "
                "the task requests only the ending replacement. ") * 5
    target = answer["submission"]["evidence"] if submission else answer
    target["observation"] = repeated[:512]
    assert parse_review(json.dumps(answer), {3, 4, 7}) == answer
    target["observation"] = repeated
    with pytest.raises(ValueError, match="at most 512 characters"):
        parse_review(json.dumps(answer), {3, 4, 7})


def test_schema_limits_all_references_and_seals_atomic_objects():
    schema = json_schema({3, 4, 7})
    fields = schema["properties"]["field_readings"]
    reading = fields["items"]
    before = reading["properties"]["before"]["anyOf"][1]
    submission = schema["properties"]["submission"]
    evidence = submission["properties"]["evidence"]["anyOf"][1]
    assert fields["minItems"] == 1
    assert all(obj["additionalProperties"] is False for obj in
               (schema, reading, before, submission, evidence))
    assert before["properties"]["step"] == {"type": "integer", "enum": [3, 4, 7]}
    assert evidence["properties"]["step"] == before["properties"]["step"]
    assert schema["properties"]["result_steps"]["items"] == before["properties"]["step"]
    assert schema["properties"]["evidence_steps"]["items"] == before["properties"]["step"]
    assert schema["properties"]["observation"]["maxLength"] == 512
    assert evidence["properties"]["observation"]["maxLength"] == 512
