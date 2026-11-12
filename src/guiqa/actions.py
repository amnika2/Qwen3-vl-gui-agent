import json
import math
import re

from jsonschema import ValidationError, validate

KEYS = {"Enter", "Tab", "Escape", "Backspace", "Delete", "ArrowUp", "ArrowDown",
        "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown", "Control+a", "Control+r"}

MAX_SCROLL_PIXELS = 2716

BROWSER_ACTION_SCHEMAS = {
    "click": {"properties": {"coordinate": {"type": "array", "minItems": 2, "maxItems": 2,
                "items": {"type": "number", "minimum": 0, "maximum": 1000}}}, "required": ["coordinate"]},
    "type": {"properties": {"text": {"type": "string", "maxLength": 2000},
                            "input_method": {"enum": ["keyboard"]}}, "required": ["text"]},
    "press": {"properties": {"key": {"enum": sorted(KEYS)}}, "required": ["key"]},
    "scroll": {"properties": {"pixels": {"type": "integer", "minimum": -MAX_SCROLL_PIXELS,
                                          "maximum": MAX_SCROLL_PIXELS}}, "required": ["pixels"]},
    "wait": {"properties": {"seconds": {"type": "number", "minimum": 0, "maximum": 5}},
             "required": ["seconds"]},
    "navigate": {"properties": {"url": {"type": "string", "minLength": 1}}, "required": ["url"]},
    "select": {"properties": {"selector": {"type": "string", "minLength": 1},
                              "label": {"type": "string"}, "value": {"type": "string"}},
               "required": ["selector"], "oneOf": [{"required": ["label"]}, {"required": ["value"]}]},
    "upload": {"properties": {"selector": {"type": "string", "minLength": 1},
                              "file": {"type": "string", "minLength": 1}}, "required": ["selector", "file"]},
}
BROWSER_ACTION_SCHEMAS = {kind: {"type": "object", **schema, "additionalProperties": False}
                          for kind, schema in BROWSER_ACTION_SCHEMAS.items()}


def browser_action(action):
    return {"action": action["action"], **{key: action[key]
            for key in BROWSER_ACTION_SCHEMAS[action["action"]]["properties"] if key in action}}


def parse_action(raw: str, available_steps: set[int], normalize_reload=False, tools=None,
                 native_controls=None, available_files=None):
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
    if kind == "press" and normalize_reload and action.get("key") == "F5":
        action["key"] = "Control+r"
    if kind in BROWSER_ACTION_SCHEMAS:
        _validate_browser_action(action)
    elif kind != "finish":
        raise ValueError("action must be " + ", ".join([*BROWSER_ACTION_SCHEMAS, "finish"]))
    if native_controls is not None and kind in {"select", "upload"}:
        if not any(item["action"] == kind and item["selector"] == action["selector"] for item in native_controls):
            raise ValueError("choose a supplied native control tool")
    if available_files is not None and kind == "upload" and action["file"] not in available_files:
        raise ValueError("choose a supplied upload file")
    if kind == "finish":
        if action.get("verdict") not in {"pass", "fail", "blocked"}:
            raise ValueError("verdict must be pass, fail or blocked")
        if not isinstance(action.get("observation"), str) or not action["observation"].strip():
            raise ValueError("finish requires an observation")
        steps = action.get("evidence_steps")
        if not isinstance(steps, list) or not steps or any(type(v) is not int or v not in available_steps for v in steps):
            raise ValueError("cite existing screenshot step numbers")
    return action


def _validate_browser_action(action):
    arguments = browser_action(action)
    kind = arguments.pop("action")
    try:
        validate(arguments, BROWSER_ACTION_SCHEMAS[kind])
    except ValidationError as error:
        field = ".".join(map(str, error.path))
        raise ValueError(f"{kind} {field}: {error.message}") from error
    numbers = arguments["coordinate"] if kind == "click" else [arguments["seconds"]] if kind == "wait" else []
    if any(not math.isfinite(value) for value in numbers):
        raise ValueError(f"{kind} requires finite numbers")
