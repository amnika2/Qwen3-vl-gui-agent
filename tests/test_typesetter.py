from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from playwright.sync_api import sync_playwright

from guiqa.oracle import judge
from guiqa.training_cases import TypesetterObserver


@pytest.mark.parametrize("first_visible,final_value,verdict", [
    (True, "Shoremark Edited", "fail"),
    (True, "Shoremark Final", "pass"),
    (False, "Shoremark Edited", "inconclusive"),
])
def test_second_edit_requires_visible_first_customization(first_visible, final_value, verdict):
    fixture = {"key": "Shoremark", "area": "#logo", "first": "Shoremark Edited",
               "input": "Shoremark Final", "expected": "Shoremark Final"}
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            page = browser.new_page()
            page.route("http://guiqa.test/", lambda route: route.fulfill(
                content_type="text/html", body='<h1 id="logo">Shoremark</h1>'))
            page.goto("http://guiqa.test/")
            observer = TypesetterObserver(page, fixture)
            observer.capture(0, "initial.png")

            for value, step in [(fixture["first"], 5), (fixture["input"], 11)]:
                observer.step = step - 1
                observer._response(SimpleNamespace(
                    url="http://guiqa.test/index.php/Admin_Theme_Content/Text", status=200,
                    request=SimpleNamespace(method="POST", headers={"content-type": "application/x-www-form-urlencoded"},
                        post_data=urlencode({"cmd": "savetext", "key": fixture["key"], "value": value}))))
                observer._response(SimpleNamespace(url="http://guiqa.test/", status=200,
                    request=SimpleNamespace(method="GET", is_navigation_request=lambda: True)))
                if step == 5:
                    visible = fixture["first"] if first_visible else fixture["key"]
                else:
                    visible = final_value
                page.locator("#logo").evaluate("(element, text) => element.textContent = text", visible)
                observer.capture(step + 1, f"{step + 1}.png")

            assert judge(observer.evidence)[0] == verdict
            first_events = [event for event in observer.evidence.events if event["kind"] == "first_reopened_area"]
            assert bool(first_events) == first_visible
