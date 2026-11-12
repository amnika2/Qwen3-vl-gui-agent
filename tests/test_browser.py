import json

from playwright.sync_api import sync_playwright

from guiqa.actions import parse_action
from guiqa.browser import VIEWPORT, browser_smoke, execute
from guiqa.model import FakeModel
from guiqa.oracle import ProfileObserver
from guiqa.dataset26_runtime import DatasetRecorder


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


def test_dataset26_navigation_selection_upload_and_keyboard_events(tmp_path):
    document = tmp_path / "evidence.txt"
    document.write_text("uploaded evidence")
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            page = browser.new_page(viewport=VIEWPORT)
            page.route("http://guiqa.test/actions", lambda route: route.fulfill(content_type="text/html", body='''
<body style="height:5000px">
<input id="typed" onkeyup="document.querySelector('#keyup').textContent=this.value">
<span id="keyup"></span>
<select id="choice"><option value="1">One</option><option value="2">Two</option></select>
<input id="file" type="file" onchange="const reader=new FileReader();reader.onload=()=>document.querySelector('#uploaded').textContent=reader.result;reader.readAsText(this.files[0])">
<p id="uploaded"></p>
</body>'''))
            run = lambda action: execute(page, parse_action(json.dumps(action), {0}))
            run({"action": "navigate", "url": "http://guiqa.test/actions"})
            assert page.url == "http://guiqa.test/actions"
            run({"action": "select", "selector": "#choice", "label": "Two"})
            assert page.locator("#choice").input_value() == "2"
            run({"action": "upload", "selector": "#file", "file": str(document)})
            page.get_by_text("uploaded evidence", exact=True).wait_for()
            page.locator("#typed").focus()
            run({"action": "type", "text": "new", "input_method": "keyboard"})
            assert page.locator("#typed").input_value() == "new"
            assert page.locator("#keyup").inner_text() == "new"
            run({"action": "scroll", "pixels": 2716})
            assert page.evaluate("window.scrollY") > 0


def test_dataset26_recorder_executes_and_records_a_shared_browser_action(tmp_path):
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            page = browser.new_page(viewport=VIEWPORT)
            page.route("http://guiqa.test/recorded", lambda route: route.fulfill(
                content_type="text/html", body='<h1>Recorded page</h1><input id="value">'))
            recorder = DatasetRecorder(page, tmp_path, {"case_id": "sweeper-027"}, "Open the requested page")
            assert recorder.step({"action": "navigate", "url": "http://guiqa.test/recorded"}) == 1
            page.locator("#value").focus()
            assert recorder.step({"action": "type", "text": "recorded", "input_method": "keyboard"}) == 2
            assert page.locator("#value").input_value() == "recorded"
            trace = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
            assert [row["description"] for row in trace[1:]] == ["navigate", "type"]
            assert [row["step"] for row in trace] == [0, 1, 2]
            assert all((tmp_path / row["screenshot"]).is_file() for row in trace)
