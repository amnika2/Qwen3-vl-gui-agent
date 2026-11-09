import json
import shutil
import subprocess
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright

from guiqa.extra_instances import AnchorInstance


def main():
    root = Path(__file__).resolve().parents[2]
    directory = root / "runs" / ("anchor-probe-" + uuid.uuid4().hex[:8])
    instance = AnchorInstance(root, directory, "A", 13208)
    try:
        try:
            instance.start()
        except RuntimeError:
            response = instance.http.get(instance.url + instance.health_path, timeout=5)
            (directory / "startup-response.html").write_text(response.text)
            print(response.status_code, response.text[:1000], flush=True)
            raise
        with sync_playwright() as playwright:
            with playwright.chromium.launch() as browser:
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.goto(instance.url + instance.health_path, wait_until="networkidle")
                page.screenshot(path=str(directory / "login.png"))
                print(directory, page.title(), flush=True)
                print(page.locator("input").evaluate_all("es=>es.map(e=>({name:e.name,type:e.type}))"), flush=True)
                fixture = json.loads((root / "cases/sweeper-008/fixtures.private.json").read_text())["target"]
                page.locator('input[name=user]').fill(fixture["username"])
                page.locator('input[name=pass]').fill(fixture["password"])
                page.get_by_role("button", name="Login", exact=True).click()
                page.wait_for_timeout(300)
                page.screenshot(path=str(directory / "after-login.png"))
                print("LOGIN", page.url, page.locator("body").inner_text()[:1800], flush=True)
    finally:
        instance.close()




if __name__ == "__main__":
    main()
