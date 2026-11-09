from pathlib import Path
import time

from playwright.sync_api import TimeoutError as BrowserTimeout

VIEWPORT = {"width": 1280, "height": 900}


def login(page, url, fixture):
    page.goto(url + "/admin/account.php?action=signin", wait_until="networkidle")
    page.locator("#user").fill(fixture["username"])
    page.locator("#pw").fill(fixture["password"])
    page.locator("button[type=submit]").click()
    page.wait_for_url(lambda value: "action=signin" not in str(value))
    page.goto(url + "/admin/blogger.php", wait_until="networkidle")
    page.locator("input[name=name]").wait_for()


def appointments_login(page, url, fixture):
    page.goto(url + "/index.php/login", wait_until="networkidle")
    page.locator("#username").fill(fixture["username"])
    page.locator("#password").fill(fixture["password"])
    page.locator("#login").click()
    page.wait_for_url(lambda value: str(value).split("?")[0].endswith("/calendar"))
    page.goto(url + "/index.php/providers", wait_until="networkidle")
    page.locator(".provider-row").filter(has_text="jane@pippins.test").wait_for()


def akaunting_login(page, url, fixture):
    page.goto(url + "/auth/login", wait_until="domcontentloaded")
    page.locator("input[name=email]").fill(fixture["username"])
    page.locator("input[name=password]").fill(fixture["password"])
    page.locator("button[type=submit]").click()
    page.wait_for_url(lambda value: "/auth/login" not in str(value), wait_until="domcontentloaded")
    page.goto(url + "/1/common/items/create", wait_until="domcontentloaded")
    page.locator("input[name=sale_price]").wait_for()
    settle(page)


def settle(page):
    try:
        page.wait_for_load_state("networkidle", timeout=2000)
    except BrowserTimeout:
        pass
    page.wait_for_timeout(150)


def execute(page, action):
    kind = action["action"]
    result = {"action": kind}
    if kind == "click":
        x, y = to_css(action["coordinate"], VIEWPORT)
        page.mouse.click(x, y)
        result.update(raw_coordinate=action["coordinate"], css_coordinate=[x, y])
    elif kind == "type":
        page.keyboard.insert_text(action["text"])
    elif kind == "press":
        # CDP keyboard events do not invoke Chromium's address-bar shortcuts in headless mode.
        if action["key"] == "Control+r":
            page.reload(wait_until="domcontentloaded")
        else:
            page.keyboard.press(action["key"])
    elif kind == "scroll":
        page.mouse.wheel(0, action["pixels"])
    elif kind == "wait":
        page.wait_for_timeout(action["seconds"] * 1000)
    settle(page)
    return result


def to_css(coordinate, viewport):
    return [coordinate[0] * (viewport["width"] - 1) / 1000,
            coordinate[1] * (viewport["height"] - 1) / 1000]


def browser_smoke(output: Path):
    from playwright.sync_api import sync_playwright

    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
            positions = [(20, 20), (1260, 20), (20, 880), (1260, 880), (640, 450)]
            buttons = "".join(f'<button style="position:absolute;left:{x-15}px;top:{y-15}px;width:30px;height:30px" onclick="this.dataset.hit=1">{i}</button>' for i, (x, y) in enumerate(positions))
            page.set_content(buttons)
            for x, y in positions:
                execute(page, {"action": "click", "coordinate": [x / 1279 * 1000, y / 899 * 1000]})
            hits = page.locator('button[data-hit="1"]').count()
            page.screenshot(path=str(output / "browser-smoke.png"))
            if hits != 5:
                raise AssertionError(f"expected five corner/center clicks, got {hits}")
            return {"corner_center_clicks": hits, "viewport": VIEWPORT, "dpr": 1,
                    "chromium": browser.version}
