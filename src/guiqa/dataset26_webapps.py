"""Real browser collection for the eight Python, Node and Go dataset applications."""

import argparse
import json
import time
import traceback
from pathlib import Path

from playwright.sync_api import sync_playwright

from .browser import VIEWPORT, settle
from . import dataset26_more_webapps as more_webapps
from .dataset26_extra_testapps import evershop_flow, wger_flow


ROOT = Path(__file__).resolve().parents[2]
CASES = {
    "sweeper-151": {"split": "train", "category": "V", "pairs": 25, "slug": "pocketbase"},
    "sweeper-102": {"split": "train", "category": "X", "pairs": 25, "slug": "linkding"},
    "sweeper-083": {"split": "validation", "category": "F", "pairs": 15, "slug": "ihatemoney"},
    "sweeper-020": {"split": "validation", "category": "V", "pairs": 15, "slug": "changedetection.io"},
    "sweeper-192": {"split": "test", "category": "V", "pairs": 10, "slug": "wger"},
    "sweeper-045": {"split": "test", "category": "O", "pairs": 10, "slug": "evershop"},
    "sweeper-009": {"split": "test", "category": "L", "pairs": 10, "slug": "answer"},
    "sweeper-043": {"split": "test", "category": "X", "pairs": 10, "slug": "etherpad"},
}
FAMILIES = {
    "sweeper-151": "pocketbase.unique_constraint_field_attribution",
    "sweeper-102": "linkding.import_bookmarks",
    "sweeper-083": "ihatemoney.create_expense_bill",
    "sweeper-020": "changedetection.validate_custom_check_interval",
    "sweeper-192": "wger.validate_profile_update",
    "sweeper-045": "evershop.inspect_saved_product_quantity",
    "sweeper-009": "answer.accept_answer",
    "sweeper-043": "etherpad.import_document",
}
CASES.update(more_webapps.CASES)
FAMILIES.update(more_webapps.FAMILIES)


def main():
    args = arguments()
    jobs = collection_jobs(args)
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output / ("webapps-" + "-".join(args.case or CASES)
                                   + ("-" + args.build if args.build else "") + ".json")
    manifest = {"stage": "dataset26-webapps-v1", "planned": len(jobs), "completed": [], "failed": []}
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            for spec in jobs:
                run_directory = args.output / spec["case_id"] / (spec["instance_id"] + "-" + spec["build"])
                saved = run_directory / "result.json"
                if saved.exists() and json.loads(saved.read_text()).get("complete"):
                    manifest["completed"].append(json.loads(saved.read_text()))
                    continue
                result = collect_job(browser, spec, run_directory, args.port)
                manifest["completed"].append(result)
                write_json(manifest_path, manifest)
                print(json.dumps(result, ensure_ascii=False), flush=True)
    manifest["complete"] = len(manifest["completed"]) == len(jobs)
    write_json(manifest_path, manifest)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", choices=tuple(CASES))
    parser.add_argument("--output", type=Path, default=ROOT / "runs/dataset26-v1/collections")
    parser.add_argument("--port", type=int, default=14600)
    parser.add_argument("--build", choices=("A", "B"))
    parser.add_argument("--pairs", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--start-index", type=int, default=0)
    return parser.parse_args()


def collection_jobs(args):
    jobs = []
    for case_id in args.case or CASES:
        scenarios = build_scenarios(case_id)
        end = args.start_index + args.pairs if args.pairs is not None else None
        for scenario in scenarios[args.start_index:end]:
            for build in ([args.build] if args.build else ["A", "B"]):
                jobs.append({**scenario, "build": build})
    return jobs[:args.limit]


def build_scenarios(case_id, split=None):
    definition = CASES[case_id]
    partition = split or definition["split"]
    result = []
    for index in range(definition["pairs"]):
        preservation = index % 5 == 4 and case_id not in {"sweeper-083", "sweeper-025"}
        condition = "preservation" if preservation else "target"
        if case_id == "sweeper-020" and condition == "target":
            condition = ("zero_interval", "empty_interval", "mixed_empty_zero")[index % 3]
        result.append({"case_id": case_id, "split": partition, "category": definition["category"],
                       "index": index, "instance_id": f"d26-{case_id}-{index + 1:03d}",
                       "task_family_id": FAMILIES[case_id], "condition": condition})
    return result


def collect_job(browser, spec, directory, port):
    from .dataset26_images import ImageInstance

    directory.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    if spec["case_id"] == "sweeper-151":
        from .dataset26_pocketbase import PocketbaseInstance
        instance = PocketbaseInstance(ROOT, directory / "private/instance", spec["build"], port)
    elif spec["case_id"] == "sweeper-025":
        from .dataset26_cockpit import CockpitInstance
        instance = CockpitInstance(ROOT, directory / "private/instance", spec["build"], port)
    elif spec["case_id"] == "sweeper-083":
        from .dataset26_ihatemoney import IhatemoneyInstance
        instance = IhatemoneyInstance(ROOT, directory / "private/instance", spec["build"], port)
    elif spec["case_id"] == "sweeper-037":
        from .dataset26_dotclear import DotclearInstance
        instance = DotclearInstance(ROOT, directory / "private/instance", spec["build"], port)
    elif spec["case_id"] == "sweeper-009":
        from .dataset26_answer import AnswerInstance
        instance = AnswerInstance(ROOT, directory / "private/instance", spec["build"], port)
    else:
        instance = ImageInstance(ROOT, directory / "private/instance", spec["case_id"], spec["build"], port)
    try:
        instance.start()
        result = collect(browser, instance, spec, directory)
        result["elapsed_seconds"] = time.monotonic() - started
        write_json(directory / "result.json", result)
        return result
    except Exception as error:
        write_json(directory / "failure.json", {"complete": False, **spec, "error": str(error),
                                                  "traceback": traceback.format_exc(),
                                                  "elapsed_seconds": time.monotonic() - started})
        raise
    finally:
        instance.close()


def collect(browser, instance, spec, directory):
    from .dataset26_runtime import DatasetRecorder

    cases = {row["case_id"]: row for row in map(json.loads, (
        ROOT / "vendor/SWEeper-Bench/test.jsonl").read_text().splitlines())}
    case = cases[spec["case_id"]]
    metadata = {**spec, "source_commit": case["base_commit" if spec["build"] == "A" else "reference_commit"]}
    task, fixture = task_fixture(spec)
    write_json(directory / "private/fixture.json", fixture)
    parameterize_fixture(instance, spec, fixture)
    page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
    try:
        page.set_default_timeout(15000)
        prepare_login(page, instance.url, spec["case_id"])
        recorder = DatasetRecorder(page, directory, metadata, task)
        return FLOWS[spec["case_id"]](recorder, instance.url, fixture)
    finally:
        page.close()


def task_fixture(spec):
    cid, index, condition = spec["case_id"], spec["index"], spec["condition"]
    if cid in more_webapps.CASES:
        return more_webapps.task_fixture(spec)
    token = f"d26-{index + 1:03d}"
    preservation = condition == "preservation"
    if cid == "sweeper-151":
        fixture = {"collection": "skus" if preservation else "items", "token": "SKU-" + token,
                   "plain_field": "title" if preservation else "price", "unique_field": "sku" if preservation else "price_id",
                   "first": "First " + token if preservation else str(10 + index),
                   "second": "Second " + token if preservation else str(30 + index)}
        task = (f"In {fixture['collection']}, create a record with {fixture['plain_field']}={fixture['first']!r} "
                f"and {fixture['unique_field']}={fixture['token']!r}. Attempt another with "
                f"{fixture['plain_field']}={fixture['second']!r} and the same {fixture['unique_field']}. "
                f"Inspect the visible field errors. Only {fixture['unique_field']} is unique; "
                f"{fixture['plain_field']} must not receive a uniqueness error.")
    elif cid == "sweeper-102":
        host = f"{token}.example.test"
        urls = [f"https://{host}", f"https://{host}-other"] if preservation else [f"https://{host}", f"https://{host}/"]
        fixture = {"token": token, "urls": urls, "titles": [token + " Plain", token + " Slash"],
                   "preservation": preservation}
        task = (f"Import the supplied bookmark file {token}.html through Settings → Import. "
                f"Search bookmarks for {token!r}. "
                + ("Both distinct URLs must appear as separate bookmarks." if preservation else
                   "The two source URLs differ only in the final slash; exactly one bookmark must remain after URL normalization."))
    elif cid == "sweeper-083":
        fixture = {"what": ("Train fare", "Market vegetables", "Shared electricity", "Dinner supplies", "Museum tickets")[index % 5] + " " + token,
                   "amount": f"{20 + index}.{(index * 7) % 100:02d}", "payer": "Lena Ort" if index % 2 == 0 else "Milo Kade"}
        task = (f"In Cabin Split, add an Expense bill for {fixture['what']!r}, amount {fixture['amount']}, "
                f"paid by {fixture['payer']}, shared by Lena Ort and Milo Kade. Keep Bill Type as Expense. "
                "Submit once and reopen Bills. Check that the new bill appears and there is no Bill Type validation error.")
    elif cid == "sweeper-020":
        fields = ("weeks", "days", "hours", "minutes", "seconds")
        values = {field: ("" if condition == "empty_interval" or condition == "mixed_empty_zero" and position % 2 else "0")
                  for position, field in enumerate(fields)}
        if preservation:
            values["minutes"] = str(5 + index)
        fixture = {"url": f"https://example.com/{token}-interval", "values": values, "preservation": preservation}
        task = (f"Add a watch for {fixture['url']!r}, open Edit, disable the global check interval and set "
                + ", ".join(f"{name}={value!r}" for name, value in values.items()) + ". Save once. "
                + ("The positive custom interval must save successfully." if preservation else
                   "The empty/zero custom interval must be rejected visibly with an 'At least one time interval' validation error."))
    elif cid == "sweeper-192":
        fixture = {"email": "priya.shah@test.com" if preservation else "jordan.lee@admin.com",
                   "height": str(171 + index), "first_name": "Mira" + str(index + 1),
                   "last_name": "Dataset", "preservation": preservation}
        task = (f"Open your Preferences and set Email to {fixture['email']!r}, First name to {fixture['first_name']!r}, "
                f"Last name to {fixture['last_name']!r} and Height to {fixture['height']}. Save and reload Preferences. "
                + ("This unique-email edit must persist." if preservation else
                   "The email belongs to another account: the form must show an error and preserve both your previous email and height."))
    elif cid == "sweeper-045":
        fixture = {"preservation": preservation, "product": "Linen Crew Tee" if preservation else "Oxford Ash Tee",
                   "expected": str((7 if preservation else 5) + index * 2), "sku": "OXFORD_ASH_TEE_M"}
        task = (f"In Products open {fixture['product']!r}. "
                + (f"Inspect Inventory Quantity; it should show the saved quantity {fixture['expected']}." if preservation else
                   f"Open Edit for the Size M variant (SKU OXFORD_ASH_TEE_M). Inspect Qty; the saved inventory is {fixture['expected']}.")
                + " Read the form value without saving.")
    elif cid == "sweeper-009":
        fixture = {"permalink": "2" if preservation else "4", "preservation": preservation,
                   "original_title": "What is reputation and how do I earn them?" if preservation else "What is a tag?"}
        fixture["title"] = fixture["original_title"] + " " + token
        task = (f"Set SEO Permalink to {'long numeric' if preservation else 'short'} question IDs. "
                f"Open {fixture['title']!r} and accept its answer. Check that it shows Accepted with no 'Answer do not found' error.")
    else:
        words = ("hey", "oak", "sun", "elm", "bay", "fir", "sky", "sea", "ash", "ink")
        fixture = {"pad": token + "-notes", "preservation": preservation,
                   "text": f"Workshop notes {index + 1}\n" if preservation else words[index % len(words)] + "\n"}
        task = (f"Open a new pad named {fixture['pad']!r}. Through Import / Export import the supplied "
                + ("plain-text file" if preservation else "legacy .etherpad file without revision author metadata")
                + f". Check the actual editor document contains {fixture['text'].strip()!r} and the default welcome text is gone.")
    return task, fixture


def parameterize_fixture(instance, spec, fixture):
    cid = spec["case_id"]
    if cid in more_webapps.CASES:
        more_webapps.parameterize_fixture(instance, spec, fixture)
    elif cid == "sweeper-045":
        sku = "LINEN_CREW_TEE" if fixture["preservation"] else fixture["sku"]
        sql = ("UPDATE product_inventory SET qty=" + fixture["expected"]
               + " WHERE product_inventory_product_id=(SELECT product_id FROM product WHERE sku='" + sku + "');")
        instance.run(["psql", "-h", "127.0.0.1", "-p", "5432", "-U", "evershop", "-d", "evershop", "-c", sql])
    elif cid == "sweeper-009":
        import sqlite3
        with sqlite3.connect(instance.live_db) as database:
            database.execute("UPDATE question SET title=? WHERE title=?", (fixture["title"], fixture["original_title"]))


def prepare_login(page, url, case_id):
    if case_id in more_webapps.CASES:
        return more_webapps.prepare_login(page, url, case_id)
    if case_id == "sweeper-151":
        page.goto(url + "/_/", wait_until="domcontentloaded")
        page.locator('input[type="email"]').fill("jordan.reed@basestack.test")
        page.locator('input[type="password"]').fill("SoftLedger8!pine")
        page.locator('button[type="submit"]').click()
        page.get_by_text("Collections", exact=True).wait_for()
    elif case_id == "sweeper-102":
        page.goto(url + "/login/", wait_until="domcontentloaded")
        page.locator('input[name="username"]').fill("PriyaShah")
        page.locator('input[name="password"]').fill("meowmix7")
        page.locator('button[type="submit"], input[type="submit"]').click()
        page.wait_for_url(lambda value: "/login" not in str(value))
    elif case_id == "sweeper-083":
        page.goto(url + "/", wait_until="domcontentloaded")
        form = page.locator("#authentication-form")
        form.locator('input[name="id"]').fill("cabin-split")
        form.locator('input[name="password"]').fill("wrenLoop5!")
        form.locator('button[type="submit"]').click()
        page.locator("#bill_table").wait_for()
    elif case_id == "sweeper-192":
        page.goto(url + "/en/user/login", wait_until="domcontentloaded")
        page.locator('input[name="username"]').fill("priya-shah")
        page.locator('input[name="password"]').fill("tapeDeck42!")
        page.locator('button[type="submit"], input[type="submit"]').click()
        page.wait_for_url(lambda value: "/user/login" not in str(value))
    elif case_id == "sweeper-045":
        page.goto(url + "/admin/login", wait_until="networkidle")
        page.locator('input[name="email"]').fill("ash.rowan@test.com")
        page.locator('input[name="password"]').fill("twillSoap6!")
        page.get_by_role("button", name="SIGN IN", exact=True).click()
        page.wait_for_url(lambda value: "/admin/login" not in str(value))
    elif case_id == "sweeper-009":
        page.goto(url + "/users/login", wait_until="domcontentloaded")
        page.locator('input[type="email"], input[name="email"]').fill("siteadmin@communityanswers.local")
        page.locator('input[type="password"]').fill("SiteAdmin#2024")
        page.locator('button[type="submit"]').click()
        page.wait_for_url(lambda value: "/users/login" not in str(value))
    else:
        page.goto(url + "/", wait_until="domcontentloaded")
    settle(page)


def pocketbase_flow(r, url, fixture):
    page = r.page
    r.click(page.get_by_role("link", name=fixture["collection"], exact=True))
    r.click(page.get_by_text("New record", exact=True).first)
    r.fill(page.get_by_label(fixture["plain_field"], exact=True), fixture["first"])
    r.fill(page.get_by_label(fixture["unique_field"], exact=True), fixture["token"])
    r.click(page.get_by_role("button", name="Create", exact=True))
    page.get_by_text("New record", exact=True).first.wait_for()
    r.click(page.get_by_text("New record", exact=True).first)
    r.fill(page.get_by_label(fixture["plain_field"], exact=True), fixture["second"])
    r.fill(page.get_by_label(fixture["unique_field"], exact=True), fixture["token"])
    before = r.index
    r.click(page.get_by_role("button", name="Create", exact=True))
    page.locator(".help-block-error").first.wait_for()
    plain = page.get_by_label(fixture["plain_field"], exact=True).locator("xpath=..")
    unique = page.get_by_label(fixture["unique_field"], exact=True).locator("xpath=..")
    plain_error, unique_error = plain.inner_text(), unique.inner_text()
    checks = [check("unique_field_error", "Value must be unique", unique_error,
                    "Value must be unique" in unique_error, r.index),
              check("plain_field_has_no_unique_error", False, "Value must be unique" in plain_error,
                    "Value must be unique" not in plain_error, r.index)]
    return finish(r, checks, [before, r.index],
                  f"{fixture['unique_field']} field: {unique_error!r}; {fixture['plain_field']} field: {plain_error!r}.")


def linkding_flow(r, url, fixture):
    page = r.page
    r.goto(url + "/settings/general")
    native = page.locator('input[name="import_file"]')
    r.scroll(int(native.bounding_box()["y"] - VIEWPORT["height"] / 2))
    path = r.directory / "input-files" / (fixture["token"] + ".html")
    path.parent.mkdir(exist_ok=True)
    html = ('<!DOCTYPE NETSCAPE-Bookmark-file-1><META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">'
            '<TITLE>Bookmarks</TITLE><H1>Bookmarks</H1><DL><p>')
    html += "".join(f'<DT><A HREF="{href}" ADD_DATE="{1700000000 + index}" TAGS="d26tag{index}">{title}</A>'
                    for index, (href, title) in enumerate(zip(fixture["urls"], fixture["titles"])))
    path.write_text(html + "</DL><p>")
    r.upload('input[name="import_file"]', path)
    r.click(native.locator("xpath=ancestor::form").locator('input[type="submit"]'), navigation=True)
    submitted = r.index
    r.goto(url + "/bookmarks?q=" + fixture["token"])
    page.locator(".bookmark-list, .bookmarks").first.wait_for()
    body = page.locator("body").inner_text()
    found = [title for title in fixture["titles"] if title in body]
    expected = 2 if fixture["preservation"] else 1
    checks = [check("matching_bookmark_count", expected, len(found), len(found) == expected, r.index)]
    return finish(r, checks, [submitted, r.index], f"Visible imported bookmark titles: {found!r}.")


def ihatemoney_flow(r, url, fixture):
    page = r.page
    r.click(page.get_by_role("link", name="Add a new bill", exact=True))
    page.locator('input[name="what"]').wait_for()
    r.fill(page.locator('input[name="what"]'), fixture["what"])
    r.fill(page.locator('input[name="amount"]'), fixture["amount"])
    r.select('select[name="payer"]', label=fixture["payer"])
    r.click(page.get_by_role("link", name="Everyone", exact=True))
    before = r.index
    r.click(page.get_by_role("button", name="Submit", exact=True))
    body = page.locator("body").inner_text()
    error = "Invalid Choice" in body or "could not coerce" in body
    response = r.index
    r.goto(url + "/cabin-split/")
    table = page.locator("#bill_table").inner_text()
    found = fixture["what"] in table
    checks = [check("no_bill_type_error", False, error, not error, response),
              check("new_bill_visible", fixture["what"], table, found, r.index)]
    return finish(r, checks, [before, response, r.index],
                  f"Submit showed a Bill Type error: {error}. Reopened bills table: {table!r}.")


def changedetection_flow(r, url, fixture):
    page = r.page
    r.fill(page.locator('input[name="url"]'), fixture["url"])
    r.click(page.locator('input[type="submit"], button[type="submit"]').first)
    row = page.locator("tr").filter(has_text=fixture["url"])
    r.click(row.get_by_role("link", name="Edit", exact=True))
    control = page.locator('input[name="time_between_check_use_default"]')
    if control.is_checked():
        r.click(control)
    for name, value in fixture["values"].items():
        r.fill(page.locator(f'input[name="time_between_check-{name}"]'), value)
    r.press("Tab")
    before = r.index
    r.click(page.get_by_role("button", name="Save", exact=True))
    body = page.locator("body").inner_text()
    error = "At least one time interval" in body
    updated = "Updated watch" in body
    matched = updated and not error if fixture["preservation"] else error and not updated
    checks = [check("interval_validation", "accepted" if fixture["preservation"] else "rejected",
                    {"error_visible": error, "updated_visible": updated}, matched, r.index)]
    return finish(r, checks, [before, r.index],
                  f"After Save: interval error visible={error}; Updated watch visible={updated}.")


def answer_flow(r, url, fixture):
    page = r.page
    r.goto(url + "/admin/seo")
    r.select('select[name="permalink"], select#permalink', value=fixture["permalink"])
    r.click(page.get_by_role("button", name="Save", exact=True))
    r.goto(url + "/")
    r.click(page.get_by_role("link", name=fixture["title"], exact=True))
    before = r.index
    r.click(page.get_by_text("Accept", exact=True))
    body = page.locator("body").inner_text()
    accepted = page.get_by_text("Accepted", exact=True).is_visible()
    error = "Answer do not found" in body
    checks = [check("answer_accepted", True, accepted, accepted, r.index),
              check("answer_not_found_error", False, error, not error, r.index)]
    return finish(r, checks, [before, r.index], f"Accepted control visible={accepted}; Answer do not found error={error}.")


def etherpad_flow(r, url, fixture):
    page = r.page
    r.fill(page.locator("#padname"), fixture["pad"])
    r.click(page.locator("#go2Name").locator('button[type="submit"], input[type="submit"]'))
    page.locator("#editorcontainer").wait_for()
    r.click(page.locator(".buttonicon-import_export"))
    path = r.directory / "input-files" / (fixture["pad"] + (".txt" if fixture["preservation"] else ".etherpad"))
    path.parent.mkdir(exist_ok=True)
    if fixture["preservation"]:
        path.write_text(fixture["text"])
    else:
        word = fixture["text"].strip()
        content = {"pad:testing": {"atext": {"text": fixture["text"], "attribs": "|1+4"},
                                   "pool": {"numToAttrib": {}, "nextNum": 0}, "head": 0, "savedRevisions": []},
                   "pad:testing:revs:0": {"changeset": "Z:1>3+3$" + word,
                                          "meta": {"timestamp": 1273769875976,
                                                   "atext": {"text": fixture["text"], "attribs": "|1+4"}}}}
        path.write_text(json.dumps(content, separators=(",", ":")))
    r.upload("#importfileinput", path)
    page.once("dialog", lambda dialog: dialog.accept())
    before = r.index
    r.click(page.locator("#importsubmitinput"))
    page.wait_for_timeout(1500)
    editor = page.frame_locator('iframe[name="ace_outer"]').frame_locator('iframe[name="ace_inner"]').locator("body")
    text = editor.inner_text()
    failed = page.locator("#importmessagefail:visible").count() > 0
    r.capture("Read imported editor document")
    checks = [check("editor_imported_text", fixture["text"].strip(), text,
                    fixture["text"].strip() in text, r.index),
              check("import_failure_visible", False, failed, not failed, r.index),
              check("default_welcome_removed", False, "Welcome to Etherpad" in text,
                    "Welcome to Etherpad" not in text, r.index)]
    return finish(r, checks, [before, r.index], f"Editor document: {text!r}; Import failed visible={failed}.")


def check(name, expected, actual, matched, step):
    return {"name": name, "expected": expected, "actual": actual, "matched": matched, "step": step}


def finish(recorder, checks, evidence_steps, observation):
    verdict = "pass" if all(item["matched"] for item in checks) else "fail"
    return recorder.finish(verdict, observation, evidence_steps=list(dict.fromkeys(evidence_steps)), checks=checks)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


FLOWS = {"sweeper-151": pocketbase_flow, "sweeper-102": linkding_flow, "sweeper-083": ihatemoney_flow,
         "sweeper-020": changedetection_flow, "sweeper-192": wger_flow, "sweeper-045": evershop_flow,
         "sweeper-009": answer_flow, "sweeper-043": etherpad_flow}
FLOWS.update(more_webapps.FLOWS)


if __name__ == "__main__":
    main()
