import json
from types import SimpleNamespace

from PIL import Image

from guiqa.control import review_guard
from guiqa.model import BUSINESS_OPERATION_PROMPT, BUSINESS_REVIEW_PROMPT, PROMPT, Qwen, REVIEW_PROMPT
from guiqa.sft_data import SftRecorder


def rejected(verdict="fail", expected=False):
    return {"action": "finish", "verdict": verdict, "requested_value_entered": True,
            "submission_succeeded": False, "final_record_visible": False,
            "submission_observed": True, "business_rejection_visible": True,
            "expected_rejection": expected, "observation": "Valid input was submitted; specific rejection is visible.",
            "evidence_steps": [1]}


def test_business_rejection_keeps_honest_fields_and_expected_outcome():
    assert review_guard(rejected()) is not None
    assert review_guard(rejected(), "business_outcome_v1") is None
    assert review_guard(rejected("pass", True), "business_outcome_v1") is None
    assert review_guard(rejected("pass", False), "business_outcome_v1") is not None
    missing = rejected()
    missing["submission_observed"] = False
    assert review_guard(missing, "business_outcome_v1") is not None


def test_recorded_and_inference_review_use_the_same_business_prompt(tmp_path):
    screenshot = tmp_path / "screenshots/001.png"
    screenshot.parent.mkdir()
    Image.new("RGB", (16, 16), "white").save(screenshot)
    context = SimpleNamespace(directory=tmp_path, history=[], event=lambda value: None,
        result={"case_id": "test", "workflow": "target", "actions": 1, "review_profile": "business_outcome_v1"})
    recorder = SftRecorder(context, "Submit a valid form", {"history_actions": 8})
    recorder.operation({"action": "click", "coordinate": [500, 500]})
    recorder.final_review([1], rejected())
    operation, sample = [json.loads(line) for line in (tmp_path / "sft/samples.jsonl").read_text().splitlines()]
    assert operation["messages"][0]["content"][0]["text"] == BUSINESS_OPERATION_PROMPT
    recorded_prompt = sample["messages"][0]["content"][0]["text"]
    model = Qwen({"history_actions": 8})
    model.load = lambda: None
    captured = []
    model._generate = lambda messages, remaining: captured.append(messages) or "response"
    model.assess("Submit a valid form", [(1, screenshot)], [], 1, profile="business_outcome_v1")
    assert recorded_prompt == captured[-1][0]["content"][0]["text"] == BUSINESS_REVIEW_PROMPT
    model.assess("Submit a valid form", [(1, screenshot)], [], 1)
    assert captured[-1][0]["content"][0]["text"] == REVIEW_PROMPT
    model.generate("Submit a valid form", screenshot, [], 1, profile="business_outcome_v1")
    assert captured[-1][0]["content"][0]["text"] == BUSINESS_OPERATION_PROMPT
    model.generate("Submit a valid form", screenshot, [], 1)
    assert captured[-1][0]["content"][0]["text"] == PROMPT
