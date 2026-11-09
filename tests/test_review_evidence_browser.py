import json
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import sync_playwright

from guiqa.model import Qwen
from guiqa.runner import RunContext, agent


class ScriptedReviewModel(Qwen):
    def __init__(self, actions, answer):
        super().__init__({"review_format": "evidence_v1"})
        self.actions = iter(actions)
        self.answer = answer
        self.review_messages = None

    def begin_run(self):
        pass

    def generate(self, task, screenshot, history, remaining, **options):
        return json.dumps(next(self.actions))

    def _generate(self, messages, remaining, max_new_tokens=None):
        self.review_messages = messages
        return json.dumps(self.answer)


@pytest.mark.parametrize("late_unfocused_edit", [False, True], ids=["saved-pass", "late-edit-keeps-input-proof"])
def test_review_keeps_real_input_and_records_field_readings(tmp_path, late_unfocused_edit):
    class Observer:
        private_answer = "PRIVATE_ORACLE_ANSWER_MUST_NOT_ENTER_REVIEW"

        def capture(self, step, screenshot):
            pass

    saved = {"name": "Original label", "submissions": []}
    requested = "Requested label"

    def serve(route):
        url = urlparse(route.request.url)
        if url.path == "/save":
            value = parse_qs(url.query)["name"][0]
            saved["submissions"].append(value)
            if not late_unfocused_edit:
                saved["name"] = value
            html = ('<h1>Item 42</h1><p>Save accepted</p>'
                    '<a href="/edit" style="position:absolute;left:400px;top:450px;'
                    'width:180px;height:50px;font-size:28px">Reopen item</a>')
        else:
            html = ('<h1>Item 42</h1><form action="/save">'
                    '<label style="position:absolute;left:400px;top:210px">Name</label>'
                    '<input name="name" value="' + saved["name"] + '" '
                    'style="position:absolute;left:400px;top:250px;width:450px;height:50px;font-size:28px">'
                    '<button style="position:absolute;left:400px;top:350px;width:180px;'
                    'height:50px;font-size:28px">Save</button></form>')
        route.fulfill(content_type="text/html", body=html)

    actions = [{"action": "click", "coordinate": [489, 306]},
               {"action": "press", "key": "Control+a"},
               {"action": "type", "text": requested},
               {"action": "click", "coordinate": [383, 417]},
               {"action": "click", "coordinate": [383, 528]}]
    if late_unfocused_edit:
        actions.extend([{"action": "click", "coordinate": [900, 850]},
                        {"action": "type", "text": "unfocused ignored"}])
    final_step = len(actions) + 1
    observed = "Original label" if late_unfocused_edit else requested
    answer = {"action": "finish", "verdict": "fail" if late_unfocused_edit else "pass",
              "requested_value_entered": True, "submission_succeeded": True,
              "final_record_visible": True,
              "field_readings": [{"field": "Name", "requested_value": requested,
                                  "input_value": requested, "input_step": 3,
                                  "observed_value": observed, "observed_step": final_step}],
              "submission_step": 4,
              "observation": "Item 42 was submitted, reopened and its Name inspected.",
              "evidence_steps": [3, 4, final_step]}
    actions.append({"action": "finish", "verdict": answer["verdict"],
                    "observation": "The saved item has been reopened.", "evidence_steps": [5]})
    model = ScriptedReviewModel(actions, answer)

    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.route("http://guiqa.test/**", serve)
            page.goto("http://guiqa.test/edit")
            result = {"run_id": "review-evidence-browser", "case_id": "synthetic",
                      "workflow": "target", "method": "qwen-controlled", "actions": 0,
                      "model_calls": 0, "agent_verdict": "blocked"}
            context = RunContext(tmp_path, result, Observer(), page)
            context.capture("Initial item")
            agent(context, "Set Item 42's Name to Requested label, save and reopen it.", model,
                  {"review_format": "evidence_v1", "wall_seconds": 30, "max_actions": 30})

            assert saved["submissions"] == [requested]
            assert page.locator('input[name="name"]').input_value() == observed

    assert result["field_readings"] == answer["field_readings"]
    assert result["submission_step"] == 4
    assert result["control_trigger"] == "model_proposed_finish"
    assert result["review_images"] <= 4
    frames = {frame["step"]: frame["roles"] for frame in result["review_evidence"]}
    assert 3 in frames and 4 in frames and final_step in frames
    assert any("input" in role for role in frames[3])
    text = "\n".join(item["text"] for message in model.review_messages
                     for item in message["content"] if item["type"] == "text")
    assert Observer.private_answer not in text
    assert "input_value" in text and "observed_value" in text
    assert "Screenshot step 3" in text
    if late_unfocused_edit:
        assert result["review_model_verdict"] == "fail"
        assert result["agent_verdict"] == "blocked"
        assert result["review_guard"] == "last_edit_not_submitted"
    else:
        assert result["agent_verdict"] == "pass"
        assert result["termination_reason"] == "controlled_review"
