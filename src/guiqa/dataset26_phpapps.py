"""Collect the remaining PHP application workflows in real pinned application images."""

import argparse
import json
import re
import traceback
from pathlib import Path

from playwright.sync_api import sync_playwright

from .browser import VIEWPORT, settle
from .dataset26_images import ImageInstance
from .dataset26_abantecart import AbantecartInstance
from .dataset26_php_forms import adminerevo, phpservermon, flyspray
from .dataset26_runtime import DatasetRecorder, write_json


ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "runs/dataset26-v1"
CASES = {"sweeper-004", "sweeper-146", "sweeper-055", "sweeper-049",
         "sweeper-001", "sweeper-087", "sweeper-074"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=sorted(CASES), required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--start-index", type=int, default=1)
    parser.add_argument("--end-index", type=int)
    parser.add_argument("--port", type=int, default=14700)
    parser.add_argument("--output", type=Path, default=RUN / "collections")
    args = parser.parse_args()
    plan = json.loads((RUN / "plan.json").read_text())
    pending = []
    for job in plan["jobs"]:
        if job["case_id"] != args.case:
            continue
        if job["index"] < args.start_index or (args.end_index is not None and job["index"] > args.end_index):
            continue
        path = args.output / job["case_id"] / job["run_id"] / "result.json"
        if not path.exists() or not json.loads(path.read_text()).get("complete"):
            pending.append(job)
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            for job in pending[:args.limit]:
                directory = args.output / job["case_id"] / job["run_id"]
                result = collect(browser, job, directory, args.port)
                print(json.dumps(result), flush=True)


def collect(browser, job, directory, port):
    directory.mkdir(parents=True, exist_ok=True)
    environment = {}
    if job["case_id"] == "sweeper-087":
        environment = {"INACTIVE_USER_EMAIL": f"inactive{job['index']:03d}@dataset26.test",
                       "INACTIVE_USER_NAME": f"Inactive account {job['index']:03d}",
                       "INACTIVE_USER_PASSWORD": f"OakTable{job['index']:03d}!",
                       "DEFAULT_ADMIN_EMAIL": f"admin{job['index']:03d}@dataset26.test",
                       "DEFAULT_ADMIN_PASSWORD": f"NorthBridge{job['index']:03d}!"}
    if job["case_id"] == "sweeper-001":
        instance = AbantecartInstance(ROOT, directory / "private/instance", job["build"], port)
    else:
        instance = ImageInstance(ROOT, directory / "private/instance", job["case_id"], job["build"], port,
                                 env=environment)
    context = browser.new_context(viewport=VIEWPORT, device_scale_factor=1)
    page = context.new_page()
    page.set_default_timeout(15000)
    try:
        instance.start()
        return FLOWS[job["case_id"]](page, instance, job, directory)
    except Exception as error:
        result = {**job, "complete": False, "error": str(error), "traceback": traceback.format_exc()}
        write_json(directory / "result.json", result)
        if not page.is_closed() and page.url != "about:blank":
            page.screenshot(path=str(directory / "failure.png"))
        raise
    finally:
        context.close()
        instance.close()


def recorder(page, directory, job, family, task):
    metadata = {key: job[key] for key in (
        "case_id", "split", "category", "build", "instance_id", "source_commit")}
    task_family = "account_login" if job["case_id"] == "sweeper-087" else family
    metadata["task_family_id"] = job["case_id"] + ":" + task_family
    metadata["condition_id"] = family
    return DatasetRecorder(page, directory, metadata, task)


def invoiceplane(page, instance, job, directory):
    inactive = job["index"] <= 8
    email, password = ((f"inactive{job['index']:03d}@dataset26.test", f"OakTable{job['index']:03d}!") if inactive
                       else (f"admin{job['index']:03d}@dataset26.test", f"NorthBridge{job['index']:03d}!"))
    page.goto(instance.url + "/sessions/login", wait_until="domcontentloaded")
    page.locator("#email").wait_for()
    task = (f"The InvoicePlane account {email!r} is {'inactive' if inactive else 'active'}. "
            f"Attempt to sign in with its correct password {password!r}. "
            + ("Verify authentication is rejected with a credentials error and the dashboard is inaccessible."
               if inactive else "Verify the account can reach its authenticated dashboard."))
    r = recorder(page, directory, job, "inactive_login" if inactive else "active_login", task)
    r.fill("#email", email)
    r.fill("#password", password)
    r.click(page.locator("button[type=submit]"))
    settle(page)
    body = page.locator("body").inner_text()
    dashboard = "/dashboard" in page.url and "Dashboard" in body and "Invoices" in body
    rejected = "Email or Password incorrect" in body and "/sessions/login" in page.url
    passed = rejected if inactive else dashboard
    observation = ("Authenticated dashboard is visible." if dashboard else
                   "Login page displays: " + body[-1400:])
    return r.finish("pass" if passed else "fail", observation,
                    checks=[{"requirement": "inactive account is denied" if inactive else "active account signs in",
                             "observed": "dashboard" if dashboard else "credentials rejection" if rejected else body[-500:]}])


def filegator(page, instance, job, directory):
    page.goto(instance.url, wait_until="domcontentloaded")
    page.get_by_text("Add files", exact=True).wait_for()
    filename = f"guest-evidence-{job['index']:03d}.txt"
    upload = directory / filename
    upload.write_text(f"dataset26 file evidence {job['instance_id']}\n")
    task = (f"As an anonymous FileGator guest with upload permission, upload the supplied file {filename!r}. "
            "Reload the root listing, search for the filename and verify that the same file is present.")
    r = recorder(page, directory, job, "guest_upload_persistence", task)
    r.upload('input[type="file"]', upload)
    r.wait(2)
    r.goto(instance.url)
    r.click("#search")
    r.fill('input[placeholder="Name"]', filename)
    r.wait(1)
    body = page.locator("body").inner_text()
    shown = page.locator(".modal-card-body li").filter(has_text=filename).count() > 0
    return r.finish("pass" if shown else "fail", body[-1800:],
                    checks=[{"file": filename, "visible_after_reload_and_search": shown}])


def abantecart(page, instance, job, directory):
    page.goto(instance.url + "/index.php?rt=account/login", wait_until="domcontentloaded")
    page.locator('input[name="loginname"]').fill("wrenHolt")
    page.locator('input[name="password"]').fill("peat8noll")
    page.get_by_role("button", name=re.compile("Login", re.I)).click()
    settle(page)
    filename = f"id-photo-{job['index']:03d}.txt"
    upload = directory / filename
    upload.write_text("dataset26 account document " + job["instance_id"])
    task = (f"Open Edit Account, attach the supplied {filename!r} to ID Photo and submit Continue. "
            "Reopen Edit Account and verify the same filename is displayed as a saved attachment link.")
    r = recorder(page, directory, job, "account_attachment_persistence", task)
    r.goto(instance.url + "/index.php?rt=account/edit")
    r.upload('input[name="id_photo"]', upload)
    r.click(page.get_by_role("button", name=re.compile("Continue", re.I)))
    r.goto(instance.url + "/index.php?rt=account/edit")
    r.scroll(500)
    body = page.locator("body").inner_text()
    visible = page.get_by_role("link", name=filename, exact=True).count() > 0
    return r.finish("pass" if visible else "fail", body[-1600:],
                    checks=[{"field": "ID Photo", "expected_filename": filename, "saved_link_visible": visible}])


def grocy(page, instance, job, directory):
    page.goto(instance.url, wait_until="domcontentloaded")
    page.goto(instance.url + "/purchase", wait_until="domcontentloaded")
    task = (f"Purchase two units of Cookies at Candy cupboard, mark Never overdue, and set note 'KEEP_NOTE_{job['index']:03d}'. "
            "Transfer one unit to Pantry, then inspect Stock entries filtered to Pantry. "
            "The transferred Cookies stock entry must retain the same note.")
    r = recorder(page, directory, job, "purchase_transfer_stock_note", task)
    grocy_choice(r, "#product_id_text_input", "Cookies")
    r.fill("#display_amount", "2")
    r.click('label[for="datetimepicker-shortcut"]')
    grocy_choice(r, "#location_id_text_input", "Candy cupboard")
    r.fill("#note", f"KEEP_NOTE_{job['index']:03d}")
    purchase_input = r.index
    r.click("#save-purchase-button")
    page.locator(".toast-success").wait_for()
    purchase_saved = r.capture("Purchase success confirmation")
    r.goto(instance.url + "/transfer")
    grocy_choice(r, "#product_id_text_input", "Cookies")
    r.select("#location_id_from", label="Candy cupboard (Default location)")
    r.fill("#display_amount", "1")
    r.select("#location_id_to", label="Pantry")
    r.click("#save-transfer-button")
    page.locator(".toast-success").wait_for()
    transfer_saved = r.capture("Transfer success confirmation")
    r.goto(instance.url + "/stockentries")
    r.select("#location-filter", label="Pantry")
    row = page.locator("tr").filter(has_text="Cookies").filter(has_text="Pantry").first
    text = row.inner_text()
    box = row.bounding_box()
    if box["y"] > VIEWPORT["height"] - 100:
        r.scroll(int(box["y"] - 350))
    passed = f"KEEP_NOTE_{job['index']:03d}" in text
    return r.finish("pass" if passed else "fail", "Pantry Cookies stock row: " + text,
                    evidence_steps=[purchase_input, purchase_saved, transfer_saved, r.index],
                    checks=[{"field": "stock note", "expected": f"KEEP_NOTE_{job['index']:03d}", "observed_row": text}])


def grocy_choice(recorder, selector, text):
    recorder.click(selector)
    recorder.press("Control+a")
    recorder.step({"action": "type", "text": text, "input_method": "keyboard"})
    recorder.press("Enter")




FLOWS = {"sweeper-004": adminerevo, "sweeper-146": phpservermon, "sweeper-055": flyspray,
         "sweeper-049": filegator, "sweeper-001": abantecart,
         "sweeper-087": invoiceplane, "sweeper-074": grocy}


if __name__ == "__main__":
    main()
