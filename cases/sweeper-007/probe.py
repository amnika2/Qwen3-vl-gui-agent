import hashlib
import json
import re
import shutil
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright

from guiqa.extra_instances import AmpacheInstance


def main():
    root = Path(__file__).resolve().parents[2]
    directory = root / "runs" / ("ampache-probe-" + uuid.uuid4().hex[:8])
    instance = AmpacheInstance(root, directory, "A", 13207)
    try:
        try:
            instance.start()
        except RuntimeError:
            response = instance.http.get(instance.url + "/login.php", timeout=5)
            (directory / "startup-response.html").write_text(response.text)
            print(response.status_code, response.text[:1000], flush=True)
            raise
        with sync_playwright() as playwright:
            with playwright.chromium.launch() as browser:
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.goto(instance.url + "/login.php", wait_until="networkidle")
                page.screenshot(path=str(directory / "login.png"))
                print(directory, page.title(), flush=True)
                print(page.locator("input").evaluate_all("es=>es.map(e=>({name:e.name,type:e.type}))"), flush=True)
                page.locator('input[name=username]').fill(instance.fixture["username"])
                page.locator('input[name=password]').fill(instance.fixture["password"])
                page.locator('input[type=submit]').click()
                page.goto(instance.url + "/browse.php?action=smartplaylist", wait_until="networkidle")
                page.screenshot(path=str(directory / "smart-playlists.png"))
                row = page.locator("tr").filter(has_text="Rename Me Smart").last
                print("ROW", row.inner_text(), flush=True)
                row.locator('a[id^=edit_playlist_]').click()
                page.locator('input[name=name]').fill("Renamed Smart Core4426")
                page.screenshot(path=str(directory / "edit.png"))
                with page.expect_response(lambda response: response.request.method == "POST") as response:
                    page.get_by_role("button", name="Save", exact=True).click()
                print("SAVE", response.value.status, response.value.text()[:400], flush=True)
                page.goto(instance.url + "/browse.php?action=smartplaylist", wait_until="networkidle")
                print("AFTER", page.locator('tr[id^=smartplaylist_row_]').all_inner_texts(), flush=True)
                page.screenshot(path=str(directory / "result.png"))
    finally:
        instance.close()




if __name__ == "__main__":
    main()
