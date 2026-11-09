import json
import math
import re

KEYS = {"Enter", "Tab", "Escape", "Backspace", "Delete", "ArrowUp", "ArrowDown",
        "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown", "Control+a", "Control+r"}


def parse_action(raw: str, available_steps: set[int], normalize_reload=False, tools=None):
    text = raw.strip()
    if text.startswith("<tool_call>") and text.endswith("</tool_call>"):
        text = text[len("<tool_call>"):-len("</tool_call>")].strip()
    if text.startswith("```json\n") and text.endswith("```"):
        text = text[8:-3].strip()
    action = json.loads(text)
    if not isinstance(action, dict):
        raise ValueError("return one JSON object")
    if action.get("name") == "computer_use":
        action = action["arguments"]
    if not isinstance(action, dict):
        raise ValueError("arguments must be an object")
    kind = action.get("action")
    if kind == "tool" and tools is not None:
        from jsonschema import ValidationError, validate

        name = action.get("name")
        if name not in tools:
            raise ValueError("choose an available tool name")
        if tools[name]["transport"] == "browser":
            action = {**action["arguments"], "action": tools[name]["action"]}
            kind = action["action"]
        else:
            try:
                validate(action.get("arguments"), tools[name]["input_schema"])
            except ValidationError as error:
                raise ValueError(error.message) from error
            return action
    if kind == "left_click":
        kind = action["action"] = "click"
    if kind not in {"click", "type", "scroll", "press", "wait", "finish"}:
        raise ValueError("action must be click, type, scroll, press, wait or finish")
    if kind == "click":
        point = action.get("coordinate")
        if not isinstance(point, list) or len(point) != 2 or any(
            type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1000 for v in point
        ):
            raise ValueError("coordinate must be two numbers in [0,1000]")
    if kind == "type" and (not isinstance(action.get("text"), str) or len(action["text"]) > 2000):
        raise ValueError("text must be a string with at most 2000 characters")
    if kind == "press" and normalize_reload and action.get("key") == "F5":
        action["key"] = "Control+r"
    if kind == "press" and action.get("key") not in KEYS:
        raise ValueError("unsupported key")
    if kind == "scroll" and (type(action.get("pixels")) is not int or not -1800 <= action["pixels"] <= 1800):
        raise ValueError("pixels must be an integer in [-1800,1800]; positive scrolls down")
    if kind == "wait" and (type(action.get("seconds")) not in (int, float) or
                           not math.isfinite(action["seconds"]) or not 0 <= action["seconds"] <= 5):
        raise ValueError("seconds must be in [0,5]")
    if kind == "finish":
        if action.get("verdict") not in {"pass", "fail", "blocked"}:
            raise ValueError("verdict must be pass, fail or blocked")
        if not isinstance(action.get("observation"), str) or not action["observation"].strip():
            raise ValueError("finish requires an observation")
        steps = action.get("evidence_steps")
        if not isinstance(steps, list) or not steps or any(type(v) is not int or v not in available_steps for v in steps):
            raise ValueError("cite existing screenshot step numbers")
    return action
