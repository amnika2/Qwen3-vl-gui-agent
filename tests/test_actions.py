import json

import pytest

from guiqa.actions import parse_action
from guiqa.browser import to_css


@pytest.mark.parametrize("value", [
    {"action": "click", "coordinate": [-1, 2]},
    {"action": "click", "coordinate": [True, 20]},
    {"action": "click", "coordinate": [10, float("nan")]},
    {"action": "type", "text": "a" * 2001},
    {"action": "press", "key": "Control+l"},
    {"action": "wait", "seconds": float("inf")},
    {"action": "scroll", "pixels": 20000},
    {"action": "finish", "verdict": "fail", "observation": "error", "evidence_steps": [5]},
    {"action": "exec", "text": "arbitrary code"},
])
def test_untrusted_actions_rejected(value):
    with pytest.raises(ValueError):
        parse_action(json.dumps(value), {0, 1})


def test_native_qwen_call_and_coordinates():
    value = parse_action('<tool_call>{"name":"computer_use","arguments":{"action":"left_click","coordinate":[1000,1000]}}</tool_call>', {0})
    assert to_css(value["coordinate"], {"width": 1280, "height": 900}) == [1279, 899]
    assert to_css([0, 0], {"width": 1280, "height": 900}) == [0, 0]
    assert to_css([500, 500], {"width": 1280, "height": 900}) == [639.5, 449.5]


def test_multiple_actions_rejected():
    with pytest.raises(ValueError):
        parse_action('[{"action":"wait","seconds":0},{"action":"wait","seconds":0}]', {0})
    with pytest.raises(ValueError):
        parse_action('{"action":"wait","seconds":0}\n{"action":"wait","seconds":0}', {0})


def test_controlled_f5_reload_alias_is_opt_in():
    with pytest.raises(ValueError):
        parse_action('{"action":"press","key":"F5"}', {0})
    assert parse_action('{"action":"press","key":"F5"}', {0}, normalize_reload=True) == {"action": "press", "key": "Control+r"}
