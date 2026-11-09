import json

from playwright.sync_api import sync_playwright

from guiqa.actions import parse_action
from guiqa.model import PROMPT, REVIEW_PROMPT
from guiqa.runner import RunContext, agent
from guiqa.sft_data import SftRecorder, sft_collect, user_text, review_text
from guiqa.sft_flows import FLOWS, SftFlow, click_at, normalized, press, type_text
from guiqa.oracle import Evidence


def test_user_text_matches_inference_construction():
    config = {"history_actions": 8}
    history = [{"step": i, "action": {"action": "click"}, "result": "executed"} for i in range(10)]
    text = user_text("Do the thing", history, config["history_actions"])
    assert text.startswith("Do the thing\nRecent actions (oldest first):\n")
    assert json.dumps(history[-8:]) in text
    assert "\nCurrent screenshot step: 9" in text
    empty = user_text("T", [], 8)
    assert empty.endswith("\nCurrent screenshot step: 0")


def test_review_text_matches_inference_construction():
    history = [{"step": 3, "action": None, "result": "executed"}]
    assert review_text("T", history) == 'T\nExecuted actions:\n[{"step": 3, "action": null, "result": "executed"}]'


def test_normalized_boxes_to_unit_square():
    assert normalized({"x": 0, "y": 0, "width": 100, "height": 50}) == [39, 28]
    assert normalized({"x": 1180, "y": 850, "width": 200, "height": 100}) == [999, 999]


def test_sft_collect_records_operation_and_review_samples(tmp_path):
    class Observer:
        def __init__(self):
            self.evidence = Evidence(preconditions=True, submitted=True, response_valid=True,
                reopened=True, same_object=True, expected="Fresh value", actual="Old")

        def capture(self, step, screenshot):
            pass

    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.route("http://guiqa.test/", lambda route: route.fulfill(
                content_type="text/html",
                body='<form><input id="name" value="Old" style="position:absolute;left:600px;top:400px;width:400px;height:40px;font-size:24px">'
                     '<button id="save" style="position:absolute;left:600px;top:500px">Save</button></form>'))
            page.goto("http://guiqa.test/")
            page.wait_for_selector("#name")
            result = dict(run_id="test", case_id="synthetic", workflow="target", method="sft",
                          build="B", actions=0, model_calls=0, agent_verdict="blocked")
            context = RunContext(tmp_path, result, Observer(), page)
            context.capture("initial")
            fixture = {"expected": "Fresh value"}
            steps = [click_at("#name"), press("Control+a"), type_text(fixture["expected"]),
                     click_at("#save"), press("Control+r")]
            recorder = sft_collect(context, "Save a value and inspect it", SftFlow(steps, 3, 4),
                                   {"history_actions": 8})
            assert result["termination_reason"] == "sft_recorded"
            assert recorder.rows == 6
            rows = [json.loads(line) for line in (tmp_path / "sft/samples.jsonl").read_text().splitlines()]
            operations = [row for row in rows if row["phase"] == "operation"]
            assert [row["step"] for row in operations] == [0, 1, 2, 3, 4]
            for row in operations:
                assert row["messages"][0]["content"][0]["text"] == PROMPT
                assert row["messages"][1]["content"][1]["image"].endswith(".png")
                assert (tmp_path / row["screenshot"].replace(str(tmp_path) + "/", "")).exists()
                assert parse_action(json.dumps(row["assistant"]), set(range(6)))
            assert parse_action(json.dumps(operations[2]["assistant"]), {0}) == {"action": "type", "text": "Fresh value"}
            reviews = [row for row in rows if row["phase"] == "review"]
            assert len(reviews) == 1
            assert reviews[0]["messages"][0]["content"][0]["text"] == REVIEW_PROMPT
            assert reviews[0]["assistant"]["verdict"] == "fail"
            assert reviews[0]["label_source"] == "observed_run_v2"
            assert parse_action(json.dumps(reviews[0]["assistant"]), set(range(6)))
            assert reviews[0]["screenshot_steps"] == [3, 4, 5]
            assert context.history[-1]["result"] == "executed"


def test_sft_collect_history_matches_agent_loop(tmp_path):
    class Observer:
        def __init__(self):
            self.evidence = Evidence()

        def capture(self, step, screenshot):
            pass

    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.route("http://guiqa.test/", lambda route: route.fulfill(content_type="text/html", body="<button>Save</button>"))
            page.goto("http://guiqa.test/")
            result = dict(run_id="test", case_id="synthetic", workflow="target", method="sft",
                          build="B", actions=0, model_calls=0, agent_verdict="blocked")
            context = RunContext(tmp_path, result, Observer(), page)
            context.capture("initial")
            steps = [click_at("body"), click_at("body"), click_at("body")]
            sft_collect(context, "Task", SftFlow(steps, 0, 0), {"history_actions": 8})
            assert [entry["step"] for entry in context.history] == [0, 1, 2, 3]
            assert all(entry["result"] == "executed" for entry in context.history)
            assert context.history[1]["action"]["raw_coordinate"]
            assert context.history[1]["action"]["css_coordinate"]


def test_emlog_flow_shape():
    flow = FLOWS["sweeper-041"]({"expected": "Board Pen Name"}, "B")
    page_probe = [make.__name__ if hasattr(make, "__name__") else str(make) for make in flow.steps]
    assert len(flow.steps) == 5 and flow.input_step == 3
    flow_a = FLOWS["sweeper-041"]({"expected": "Board Pen Name"}, "A")
    assert flow_a.input_step == flow.input_step
    assert "sweeper-149" not in FLOWS
    assert page_probe is not None


def test_dokuwiki_and_flatpress_flow_shape():
    dokuwiki = FLOWS["sweeper-034"]({"page_id": "wiki:welcome", "selector": "textarea[name=wikitext]",
                                     "input": "GUIQA edited"}, "A")
    assert len(dokuwiki.steps) == 5 and dokuwiki.input_step == 3 and dokuwiki.submit_step == 4
    flatpress = FLOWS["sweeper-053"]({"page_id": "about", "selector": "textarea[name=content]",
                                      "input": "GUIQA edited"}, "A")
    assert len(flatpress.steps) == 7 and flatpress.input_step == 5 and flatpress.submit_step == 6
    assert "sweeper-034" in FLOWS and "sweeper-053" in FLOWS
