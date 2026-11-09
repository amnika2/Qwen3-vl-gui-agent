import json

from playwright.sync_api import sync_playwright

from guiqa.actions import parse_action
from guiqa.browser import VIEWPORT, browser_smoke, execute
from guiqa.model import FakeModel
from guiqa.oracle import ProfileObserver


def test_corners_and_center(tmp_path):
    assert browser_smoke(tmp_path)["corner_center_clicks"] == 5


def test_fake_response_browser_loop(tmp_path):
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
            page.set_content('<input value="old" style="position:absolute;left:100px;top:100px;width:200px">')
            model = FakeModel([json.dumps(action) for action in [
                {"action": "click", "coordinate": [150 / 1279 * 1000, 110 / 899 * 1000]},
                {"action": "press", "key": "Control+a"}, {"action": "type", "text": "new"},
                {"action": "scroll", "pixels": 100}, {"action": "wait", "seconds": 0}]])
            for step in range(5):
                screenshot = tmp_path / f"{step}.png"
                page.screenshot(path=str(screenshot))
                execute(page, parse_action(model.generate("synthetic smoke", screenshot, [], 10), set(range(step + 1))))
            assert page.locator("input").input_value() == "new"


def test_reload_is_a_navigation_and_initial_evidence_survives_edit():
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            page = browser.new_page(viewport=VIEWPORT)
            navigations = []
            def respond(route):
                navigations.append(route.request.url)
                route.fulfill(body='<input name="username" value="writer"><input name="name" value="Old">', content_type="text/html")
            page.route("http://guiqa.test/admin/blogger.php", respond)
            page.goto("http://guiqa.test/admin/blogger.php")
            observer = ProfileObserver(page, {"username": "writer", "expected": "New", "object_id": 2})
            observer.capture(0, "0.png")
            page.locator("input[name=name]").fill("New")
            observer.capture(1, "1.png")
            assert observer.evidence.preconditions
            execute(page, {"action": "press", "key": "Control+r"})
            assert len(navigations) == 2
            assert page.locator("input[name=name]").input_value() == "Old"
