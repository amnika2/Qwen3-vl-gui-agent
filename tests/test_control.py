import json

from PIL import Image
from playwright.sync_api import sync_playwright

from guiqa.control import LoopControl, review_guard
from guiqa.model import FakeModel
from guiqa.runner import RunContext, action_effect_feedback, agent


def test_action_effect_feedback_uses_calibrated_thresholds():
    assert action_effect_feedback({"action": "type"}, 0.0) is not None
    assert action_effect_feedback({"action": "type"}, 0.10) is None
    assert action_effect_feedback({"action": "click"}, 0.4) is None
    assert action_effect_feedback({"action": "wait"}, 0.0) is None
    assert action_effect_feedback({"action": "type"}, None) is None


def test_repeated_save_and_alternating_page_cycles(tmp_path):
    screenshot = tmp_path / "screen.png"
    Image.new("RGB", (1280, 900), "white").save(screenshot)
    repeat = LoopControl(screenshot)
    results = [repeat.record({"action": "click", "coordinate": [301, 799]}, step, screenshot) for step in range(1, 4)]
    assert results == [None, None, "action_cycle_period_1"]
    alternate = LoopControl(screenshot)
    actions = [{"action": "click", "coordinate": [285, 559]}, {"action": "click", "coordinate": [195, 327]}] * 3
    # Different screenshots keep this check focused on the action cycle.
    results = []
    for step, action in enumerate(actions, 1):
        Image.new("RGB", (1280, 900), "white" if step % 2 else "black").save(screenshot)
        results.append(alternate.record(action, step, screenshot))
    assert results[-1] == "action_cycle_period_2"


def test_typing_sequence_resets_visual_stagnation(tmp_path):
    screenshot = tmp_path / "screen.png"
    Image.new("RGB", (1280, 900), "white").save(screenshot)
    control = LoopControl(screenshot)
    actions = [{"action": "click", "coordinate": [500, 400]}, {"action": "press", "key": "Control+a"},
               {"action": "type", "text": "New value"}, {"action": "click", "coordinate": [300, 800]}]
    assert all(control.record(action, step, screenshot) is None for step, action in enumerate(actions, 1))
    assert control.last_type_step == 3


def test_loop_enters_reload_and_judgment_without_oracle_answer(tmp_path):
    class Observer:
        def capture(self, step, screenshot):
            pass

    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.route("http://guiqa.test/", lambda route: route.fulfill(body="<button>Save</button>"))
            page.goto("http://guiqa.test/")
            result = dict(run_id="test", case_id="synthetic", workflow="target", method="qwen-controlled",
                          actions=0, model_calls=0, agent_verdict="blocked")
            context = RunContext(tmp_path, result, Observer(), page)
            context.capture("initial")
            responses = [json.dumps({"action": "click", "coordinate": [500, 500]})] * 3
            responses.append(json.dumps({"action": "finish", "verdict": "blocked", "observation": "No edit was submitted", "evidence_steps": [4]}))
            agent(context, "Save a value and inspect it", FakeModel(responses), {"wall_seconds": 30, "max_actions": 30})
            assert result["actions"] == 4
            assert result["model_calls"] == 4
            assert result["control_trigger"] == "action_cycle_period_1"
            assert result["termination_reason"] == "controlled_review"
            assert result["agent_verdict"] == "blocked"


def test_review_guard_requires_visible_trigger_and_reopen_evidence():
    incomplete = {"verdict": "fail", "requested_value_entered": True,
                  "submission_succeeded": False, "final_record_visible": True}
    assert review_guard(incomplete) == "review_evidence_not_established:submission_succeeded"
    complete = {**incomplete, "submission_succeeded": True}
    assert review_guard(complete) is None
    assert review_guard({"verdict": "blocked"}) is None


def test_review_fail_without_submission_evidence_is_guarded(tmp_path):
    class Observer:
        def capture(self, step, screenshot):
            pass

    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.route("http://guiqa.test/", lambda route: route.fulfill(body="<button>Save</button>"))
            page.goto("http://guiqa.test/")
            result = dict(run_id="test", case_id="synthetic", workflow="target", method="qwen-controlled",
                          actions=0, model_calls=0, agent_verdict="blocked")
            context = RunContext(tmp_path, result, Observer(), page)
            context.capture("initial")
            responses = [json.dumps({"action": "click", "coordinate": [500, 500]})] * 3
            responses.append(json.dumps({"action": "finish", "verdict": "fail", "observation": "Save was clicked but the old value remains",
                                         "requested_value_entered": True, "submission_succeeded": False,
                                         "final_record_visible": True, "evidence_steps": [4]}))
            agent(context, "Save a value and inspect it", FakeModel(responses), {"wall_seconds": 30, "max_actions": 30})
            assert result["review_model_verdict"] == "fail"
            assert result["agent_verdict"] == "blocked"
            assert result["review_guard"] == "review_evidence_not_established:submission_succeeded"
            assert result["termination_reason"] == "controlled_review_guarded"


def test_multiple_json_output_gets_one_targeted_retry(tmp_path):
    class Observer:
        def capture(self, step, screenshot):
            pass

    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.route("http://guiqa.test/", lambda route: route.fulfill(body="<button>Save</button>"))
            page.goto("http://guiqa.test/")
            result = dict(run_id="test", case_id="synthetic", workflow="target", method="qwen-controlled",
                          actions=0, model_calls=0, agent_verdict="blocked")
            context = RunContext(tmp_path, result, Observer(), page)
            context.capture("initial")
            click = json.dumps({"action": "click", "coordinate": [500, 500]})
            responses = [click + "\n" + click, click, click, click,
                         json.dumps({"action": "finish", "verdict": "blocked", "observation": "No valid submission", "evidence_steps": [4]})]
            agent(context, "Save a value and inspect it", FakeModel(responses), {"wall_seconds": 30, "max_actions": 30})
            assert result["termination_reason"] == "controlled_review"
            assert result["actions"] == 4
            assert result["model_calls"] == 5
            assert any("not a plan or sequence" in entry["result"] for entry in context.history)


def test_unsubmitted_last_edit_cannot_be_reported_as_fail(tmp_path):
    class Observer:
        def capture(self, step, screenshot):
            pass

    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.route("http://guiqa.test/", lambda route: route.fulfill(body="<input>"))
            page.goto("http://guiqa.test/")
            result = dict(run_id="test", case_id="synthetic", workflow="preservation", method="qwen-controlled",
                          actions=0, model_calls=0, agent_verdict="blocked")
            context = RunContext(tmp_path, result, Observer(), page)
            context.capture("initial")
            responses = [json.dumps({"action": "type", "text": "requested"}),
                         json.dumps({"action": "finish", "verdict": "fail", "observation": "Old value remains",
                                     "requested_value_entered": True, "submission_succeeded": True,
                                     "final_record_visible": True, "evidence_steps": [2]})]
            agent(context, "Save a value and inspect it", FakeModel(responses), {"wall_seconds": 30, "max_actions": 2})
            assert result["review_model_verdict"] == "fail"
            assert result["agent_verdict"] == "blocked"
            assert result["review_guard"] == "last_edit_not_submitted"


def test_unfocused_typing_is_flagged_and_focused_typing_is_not(tmp_path):
    class Observer:
        def capture(self, step, screenshot):
            pass

    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.route("http://guiqa.test/", lambda route: route.fulfill(
                content_type="text/html",
                body='<input id="name" value="Old" style="position:absolute;left:600px;top:400px;width:400px;height:40px;font-size:24px">'))
            page.goto("http://guiqa.test/")
            result = dict(run_id="test", case_id="synthetic", workflow="target", method="qwen-controlled",
                          actions=0, model_calls=0, agent_verdict="blocked")
            context = RunContext(tmp_path, result, Observer(), page)
            context.capture("initial")
            page.wait_for_selector("#name")
            click = json.dumps({"action": "click", "coordinate": [510, 460]})
            responses = [json.dumps({"action": "type", "text": "New value"}),
                         click,
                         json.dumps({"action": "press", "key": "Control+a"}),
                         json.dumps({"action": "type", "text": "New value"}),
                         click, click, click,
                         json.dumps({"action": "finish", "verdict": "blocked", "observation": "No valid submission", "evidence_steps": [8]})]
            agent(context, "Save a value and inspect it", FakeModel(responses), {"wall_seconds": 30, "max_actions": 30})
            by_step = {entry.get("step"): entry["result"] for entry in context.history if "result" in entry}
            assert "The typed text is not visible" in by_step[1]
            assert "not visible" not in by_step[4]
            assert result["termination_reason"] == "controlled_review"
