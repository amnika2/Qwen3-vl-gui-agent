import json

from PIL import Image
from playwright.sync_api import sync_playwright

from guiqa.model import FakeModel, Qwen
from guiqa.review_context import review_content
from guiqa.review_facts import facts_prompt, facts_schema
from guiqa.runner import RunContext, agent


def test_facts_inference_uses_the_training_prompt_context_and_schema(tmp_path):
    screenshot = tmp_path / "screen.png"
    Image.new("RGB", (16, 16), "white").save(screenshot)
    model = Qwen({"review_format": "evidence_facts_v1", "review_max_new_tokens": 1024})
    captured = []
    model._generate = lambda messages, remaining, **options: captured.append((messages, options)) or "response"
    frames = [{"step": 0, "roles": ["record_view_candidate"]}]
    model.assess("Inspect the record", [(0, screenshot)], [], 30, frames=frames)
    messages, options = captured[0]
    assert messages[0]["content"][0]["text"] == facts_prompt()
    expected = review_content("Inspect the record", [], frames, {0: "image"})
    assert [item["text"] for item in messages[1]["content"] if item["type"] == "text"] == [
        item["text"] for item in expected if item["type"] == "text"]
    assert options == {"max_new_tokens": 1024, "schema": facts_schema([0])}


def test_browser_review_preserves_raw_facts_and_renders_the_explanation(tmp_path):
    class Observer:
        def capture(self, step, screenshot):
            pass

    facts = {
        "action": "finish", "task_kind": "edit",
        "field_readings": [{"field": "Name", "intent": "edit", "requested_value": "New",
                            "before": None, "after": None,
                            "input_matches_request": None, "result_matches_request": None}],
        "submission": {"status": "unknown", "evidence": None},
        "result_observed": False, "result_steps": [], "expected_rejection": False,
        "verdict": "blocked", "evidence_steps": [4],
    }
    raw = json.dumps(facts)
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.route("http://guiqa.test/", lambda route: route.fulfill(body="<button>Save</button>"))
            page.goto("http://guiqa.test/")
            result = dict(run_id="test", case_id="synthetic", workflow="target", method="qwen-controlled",
                          actions=0, model_calls=0, agent_verdict="blocked")
            context = RunContext(tmp_path, result, Observer(), page)
            context.capture("initial")
            responses = [json.dumps({"action": "click", "coordinate": [500, 500]})] * 3 + [raw]
            agent(context, "Set Name to New", FakeModel(responses),
                  {"wall_seconds": 30, "max_actions": 30, "review_format": "evidence_facts_v1"})
    assert result["termination_reason"] == "controlled_review"
    assert result["agent_verdict"] == "blocked"
    assert result["submission"] == facts["submission"]
    assert result["field_readings"] == facts["field_readings"]
    assert result["observation"].startswith("Submission: unknown.")
    events = [json.loads(line) for line in (tmp_path / "model.jsonl").read_text().splitlines()]
    assert next(event for event in events if event.get("phase") == "review" and "raw_output" in event)["raw_output"] == raw
