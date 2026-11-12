import json
from types import SimpleNamespace

import pytest
from PIL import Image

from guiqa.business_review import parse_review, review_guard, select_evidence
from guiqa.actions import parse_action
from guiqa.runner import controlled_review


REQUIREMENTS = [{"id": "quantity", "object": "Size M variant Qty", "expected": "7"}]


def test_native_action_uses_the_public_control_and_file_inventory():
    controls = [{"action": "upload", "selector": "#document", "label": "Import document"}]
    upload = {"action": "upload", "selector": "#document", "file": "files/import.txt"}
    assert parse_action(json.dumps(upload), {0}, native_controls=controls,
                        available_files=["files/import.txt"]) == upload
    with pytest.raises(ValueError, match="supplied native control"):
        parse_action(json.dumps({**upload, "selector": "#invented"}), {0}, native_controls=controls)
    with pytest.raises(ValueError, match="supplied upload file"):
        parse_action(json.dumps({**upload, "file": "invented.txt"}), {0}, native_controls=controls,
                     available_files=["files/import.txt"])


def answer(status="pass", observed="7", verdict="pass"):
    return {"action": "finish", "verdict": verdict, "observation": "The identified variant shows Qty " + str(observed),
            "evidence_steps": [3], "checks": [{**REQUIREMENTS[0], "observed": observed,
            "evidence_steps": [3] if observed is not None else [], "status": status}]}


def test_read_only_review_requires_no_submission_or_edit():
    result = parse_review(json.dumps(answer()), {0, 3}, requirements=REQUIREMENTS)
    assert review_guard(result) is None


def test_cannot_omit_or_redefine_a_public_requirement():
    missing = answer()
    missing["checks"] = []
    with pytest.raises(ValueError, match="requires business checks"):
        parse_review(json.dumps(missing), {3}, requirements=REQUIREMENTS)
    changed = answer()
    changed["checks"][0]["expected"] = "0"
    with pytest.raises(ValueError, match="changed a public"):
        parse_review(json.dumps(changed), {3}, requirements=REQUIREMENTS)


def test_known_result_requires_actual_supplied_evidence():
    with pytest.raises(ValueError, match="supplied screenshot"):
        parse_review(json.dumps(answer()), {0}, requirements=REQUIREMENTS)
    with pytest.raises(ValueError, match="visible facts"):
        parse_review(json.dumps(answer(observed=None)), {3}, requirements=REQUIREMENTS)


@pytest.mark.parametrize("status,observed,verdict", [("fail", "0", "fail"), ("unknown", None, "blocked")])
def test_violation_and_missing_evidence_remain_distinct(status, observed, verdict):
    parsed = parse_review(json.dumps(answer(status, observed, verdict)), {3}, requirements=REQUIREMENTS)
    assert review_guard(parsed) is None
    parsed["verdict"] = "pass"
    assert review_guard(parsed) == "review_verdict_contradicts_checks"


def test_complex_flow_retains_nominated_intermediate_views():
    history = [{"step": step, "action": {"action": "click", "coordinate": [100, 100]} if step else None}
               for step in range(10)]
    selected = select_evidence(history, {}, 9, 9, [0, 3, 6, 9])
    assert {0, 3, 6, 9}.issubset({frame["step"] for frame in selected})


def test_runtime_review_preserves_read_only_state_and_public_contract(tmp_path):
    (tmp_path / "screenshots").mkdir()
    for step in range(4):
        Image.new("RGB", (20, 20)).save(tmp_path / f"screenshots/{step:03d}.png")
    result = {"actions": 3, "model_calls": 0, "review_profile": "business_evidence_v1",
              "review_format": "business_evidence_v1", "requirements": REQUIREMENTS}
    history = [{"step": step, "action": {"action": "click", "coordinate": [100, 100]} if step else None}
               for step in range(4)]
    def assess(task, screenshots, actions, remaining, **options):
        assert options["requirements"] == REQUIREMENTS
        assert 3 in {step for step, _ in screenshots}
        return json.dumps(answer("fail", "0", "fail"))
    context = SimpleNamespace(result=result, directory=tmp_path, history=history, event=lambda item: None)
    control = SimpleNamespace(differences={}, last_type_step=0)
    model = SimpleNamespace(assess=assess)
    import time
    controlled_review(context, "Inspect Qty without saving", model, control, {0, 1, 2, 3},
                      time.monotonic() + 10, "model_proposed_finish", [3])
    assert result["actions"] == 3
    assert result["agent_verdict"] == "fail"
    assert result["checks"][0]["observed"] == "0"
