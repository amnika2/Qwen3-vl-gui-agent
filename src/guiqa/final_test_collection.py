"""Collect fresh review evidence from the five frozen test applications."""

import argparse
import copy
import hashlib
import json
import shutil
import time
import traceback
from pathlib import Path
from urllib.parse import urlparse

import yaml
from playwright.sync_api import sync_playwright

from .artifacts import write_json
from .browser import VIEWPORT, akaunting_login, execute, login, settle
from .extra_cases import PlaylistObserver, VariableObserver, ampache_login, anchor_login
from .extra_instances import AmpacheInstance, AnchorInstance
from .native import AkauntingInstance, AppointmentsInstance, EmlogInstance
from .oracle import ItemObserver, ProfileObserver, ProviderObserver, in_view, judge, parse_usd
from .review_context import review_content
from .review_contract import parse_review, review_guard, review_prompt
from .review_scenarios import scenario_metadata
from .runner import RunContext
from .sft_flows import normalized


ROOT = Path(__file__).resolve().parents[2]


def sql_string(value):
    return "'" + str(value).replace("'", "''") + "'"


class TestAkauntingInstance(AkauntingInstance):
    def seed_test_fixture(self, fixture):
        pass


class TestAppointmentsInstance(AppointmentsInstance):
    def seed_test_fixture(self, fixture):
        first, last = fixture["provider_name"].split(" ", 1)
        self.sql("USE guiqa; UPDATE ea_users SET first_name=" + sql_string(first)
                 + ",last_name=" + sql_string(last) + ",email=" + sql_string(fixture["provider_email"])
                 + "," + fixture["field"] + "=" + sql_string(fixture["initial_value"]) + " WHERE id=2;")


class TestEmlogInstance(EmlogInstance):
    def seed_test_fixture(self, fixture):
        self.sql("USE guiqa; UPDATE emlog_user SET nickname=" + sql_string(fixture["initial_value"])
                 + " WHERE uid=" + str(fixture["object_id"]) + ";")


class TestAmpacheInstance(AmpacheInstance):
    def seed_test_fixture(self, fixture):
        self.sql("USE guiqa; UPDATE search SET name=" + sql_string(fixture["initial_name"])
                 + " WHERE name='Rename Me Smart';")
        fixture["object_id"] = int(self.sql("USE guiqa; SELECT id FROM search WHERE name="
                                            + sql_string(fixture["initial_name"]) + ";").strip())
        fixture["initial_ids"] = [int(value) for value in self.sql("USE guiqa; SELECT id FROM search;").split()]


class TestAnchorInstance(AnchorInstance):
    def seed_test_fixture(self, fixture):
        self.sql("USE guiqa; DELETE FROM anchor_meta WHERE `key` LIKE 'custom_%';")
        if fixture["category"] == "target":
            self.sql("USE guiqa; INSERT INTO anchor_meta (`key`,`value`) VALUES ("
                     + sql_string(fixture["object_id"]) + "," + sql_string(fixture["initial_value"]) + ");")


class TestPlaylistObserver(PlaylistObserver):
    def capture(self, step, screenshot):
        self.step = step
        if self.fixture["category"] == "target":
            row = self.page.locator(f"tr#smartplaylist_row_{self.fixture['object_id']}")
            cell = row.locator(".cel_playlist")
            if not cell.count() or not in_view(cell):
                return
            name = cell.inner_text().strip()
            if self.submitted_at is None and name == self.fixture["initial_name"]:
                self.evidence.preconditions = True
            if self.reloaded_at is not None:
                self.evidence.actual = name
                self.evidence.object_id = self.fixture["object_id"]
                self._observe(step, screenshot)
        else:
            if step == 0:
                self.evidence.preconditions = bool(self.page.locator("#savesearchbutton").count())
            if self.reloaded_at is None:
                return
            for row in self.page.locator("tr[id^=smartplaylist_row_]").all():
                identifier = int(row.get_attribute("id").rsplit("_", 1)[1])
                if identifier in self.fixture["initial_ids"]:
                    continue
                cell = row.locator(".cel_playlist")
                if in_view(cell) and cell.inner_text().strip().startswith(self.fixture["username"]):
                    self.evidence.actual = "created"
                    self.evidence.object_id = identifier
                    self._observe(step, screenshot)


class TestVariableObserver(VariableObserver):
    def capture(self, step, screenshot):
        self.step = step
        path = urlparse(self.page.url).path
        if step == 0 and self.fixture["category"] == "preservation":
            self.evidence.preconditions = path == self.base + "/add"
        if path != self.base + "/edit/" + self.fixture["object_id"]:
            return
        name, value = self.page.locator('input[name=key]'), self.page.locator('textarea[name=value]')
        same = name.count() and name.input_value() == self.fixture["name"]
        if same and self.submitted_at is None and value.input_value() == self.fixture["initial_value"]:
            self.evidence.preconditions = True
        if same and self.reopened_at is not None and in_view(value):
            self.evidence.actual = value.input_value()
            self._observe(step, screenshot)

    def _response(self, response):
        super()._response(response)
        if (response.request.method == "GET" and self.submitted_at is not None
                and urlparse(response.url).path == self.base + "/edit/" + self.fixture["object_id"]):
            self.reopened_at = time.monotonic()


def appointments_test_login(page, url, fixture):
    page.goto(url + "/index.php/login", wait_until="networkidle")
    page.locator("#username").fill(fixture["username"])
    page.locator("#password").fill(fixture["password"])
    page.locator("#login").click()
    page.wait_for_url(lambda value: str(value).split("?")[0].endswith("/calendar"))
    page.goto(url + "/index.php/providers", wait_until="networkidle")
    page.locator(".provider-row").filter(has_text=fixture["provider_email"]).wait_for()


APPS = {
    "akaunting": (TestAkauntingInstance, akaunting_login, ItemObserver),
    "appointments": (TestAppointmentsInstance, appointments_test_login, ProviderObserver),
    "emlog": (TestEmlogInstance, login, ProfileObserver),
    "ampache": (TestAmpacheInstance, ampache_login, TestPlaylistObserver),
    "anchor": (TestAnchorInstance, anchor_login, TestVariableObserver),
}


class TestContext(RunContext):
    def __init__(self, directory, result, observer, page, app, fixture):
        super().__init__(directory, result, observer, page)
        self.app, self.fixture = app, fixture
        self.visible, self.network = {}, []
        page.on("response", self.response)

    def response(self, response):
        if response.request.method == "POST":
            self.network.append({"step": self.result["actions"], "url": response.url,
                                 "status": response.status, "post_data": response.request.post_data})

    def act(self, action, description=None, wait_url=None, wait_selector=None):
        self.result["actions"] += 1
        outcome = execute(self.page, action)
        if wait_url is not None:
            self.page.wait_for_url(wait_url, wait_until="networkidle")
        if wait_selector is not None:
            self.page.locator(wait_selector).wait_for(state="visible")
        self.capture(description or action["action"], {**action, **outcome})
        return self.result["actions"]

    def click(self, locator, wait_url=None, wait_selector=None):
        locator.scroll_into_view_if_needed()
        return self.act({"action": "click", "coordinate": normalized(locator.bounding_box())},
                        wait_url=wait_url, wait_selector=wait_selector)

    def replace(self, locator, value):
        self.click(locator)
        self.act({"action": "press", "key": "Control+a"})
        return self.act({"action": "type", "text": value})

    def navigate(self, url):
        self.result["actions"] += 1
        self.page.goto(url, wait_until="networkidle")
        self.capture("Reopen the record", {"action": "navigate", "url": url})
        return self.result["actions"]

    def capture(self, description, action=None, raw=None, elapsed=0, error=None):
        path = super().capture(description, action, raw, elapsed, error)
        fields, fixture, page = {}, self.fixture, self.page
        if self.app == "akaunting":
            fields = {"Name": reading(page.locator('input[name=name]')),
                      "Sale Price": reading(page.locator('input[name=sale_price]'))}
            rows = page.locator('tr[data-table-list][href$="/edit"]').filter(has_text=fixture["name"])
            if rows.count() == 1:
                fields["Name"] = reading(rows.locator("td").nth(1).locator("[data-truncate-marquee]").first)
                fields["Sale Price"] = reading(rows.locator("td").nth(4).locator("[data-truncate-marquee]").first)
        elif self.app == "appointments":
            label = "Phone Number" if fixture["field"] == "phone_number" else "Mobile Number"
            fields[label] = reading(page.locator(fixture["selector"]))
        elif self.app == "emlog":
            fields = {"Nickname": reading(page.locator('input[name=name]')),
                      "Username": reading(page.locator('input[name=username]'))}
        elif self.app == "anchor":
            fields = {"Name": reading(page.locator('input[name=key]')),
                      "Value": reading(page.locator('textarea[name=value]'))}
        else:
            fields["Name"] = reading(page.locator('input[name=name]'))
            if fixture["category"] == "target":
                row = page.locator(f"#smartplaylist_row_{fixture['object_id']}")
            else:
                new_ids = [int(row.get_attribute("id").rsplit("_", 1)[1])
                           for row in page.locator('tr[id^="smartplaylist_row_"]').all()
                           if int(row.get_attribute("id").rsplit("_", 1)[1]) not in fixture["initial_ids"]]
                row = page.locator(f"#smartplaylist_row_{new_ids[-1]}") if new_ids else page.locator("#no-created-playlist")
            if fields["Name"] is None:
                fields["Name"] = reading(row.locator(".cel_playlist"))
        messages = []
        for locator in page.locator(".coco-msg-content, .toast-body, .alert, .error, .success, .notifications, .notification, .message").all():
            value = reading(locator)
            if value is not None and value["value"]:
                messages.append(value["value"])
        self.visible[self.result["actions"]] = {"fields": fields, "messages": messages,
            "url": page.url, "body_text": page.locator("body").inner_text(),
            "provider_editing": page.locator("#save-provider").is_visible() if self.app == "appointments" else None,
            "playlist_dialog_open": page.locator('input[name=name]').is_visible() if self.app == "ampache" else None}
        write_json(self.directory / "private/visible.json", self.visible)
        write_json(self.directory / "private/network.json", self.network)
        return path


def reading(locator):
    if not locator.count() or not locator.first.is_visible() or not in_view(locator.first):
        return None
    element = locator.first
    tag = element.evaluate("element => element.tagName.toLowerCase()")
    value = element.input_value() if tag in {"input", "textarea", "select"} else element.inner_text().strip()
    return {"value": value, "bounding_box": element.bounding_box(), "in_view": True}


def execute_test_flow(context, fixture, group):
    page, app, variant = context.page, group["app"], group["variant"]
    wrong = variant == "input_not_established"
    if app == "akaunting":
        context.replace(page.locator('input[name=name]'), fixture["name"])
        context.replace(page.locator('input[name=sale_price]'), "765,43" if wrong else fixture["input_amount"])
        context.replace(page.locator('input[name=purchase_price]'), "100")
        if variant == "cancelled":
            submit = context.click(page.get_by_role("link", name="Cancel", exact=True), wait_url=fixture["origin"] + "/1/common/items")
        else:
            submit = context.click(page.get_by_role("button", name="Save", exact=True), wait_url=fixture["origin"] + "/1/common/items")
        context.navigate(fixture["origin"] + "/1/common/items")
    elif app == "appointments":
        row = page.locator(".provider-row").filter(has_text=fixture["provider_email"])
        context.click(row)
        context.replace(page.locator(fixture["selector"]), fixture["initial_value"] if wrong else fixture["expected"])
        submit = context.click(page.locator("#cancel-provider" if variant == "cancelled" else "#save-provider"),
                               wait_selector=None if variant == "cancelled" else ".toast-body")
        context.act({"action": "press", "key": "Control+r"})
        context.click(row)
        page.locator(fixture["selector"]).scroll_into_view_if_needed()
        context.capture("Inspect reopened provider field")
    elif app == "emlog":
        context.replace(page.locator('input[name=name]'), fixture["initial_value"] if wrong else fixture["expected"])
        if variant == "cancelled":
            submit = context.act({"action": "press", "key": "Control+r"})
        else:
            submit = context.click(page.locator("#submit_form"))
        context.act({"action": "press", "key": "Control+r"})
    elif app == "anchor":
        if fixture["category"] == "target":
            context.click(page.locator(f'a[href$="/edit/{fixture["object_id"]}"]'))
        else:
            context.replace(page.locator('input[name=key]'), fixture["name"])
        context.replace(page.locator('textarea[name=value]'), fixture["initial_value"] if wrong else fixture["expected"])
        if variant == "cancelled":
            submit = context.click(page.get_by_role("link", name="Cancel", exact=True))
        else:
            submit = context.click(page.get_by_role("button", name="Update" if fixture["category"] == "target" else "Save", exact=True))
        context.navigate(fixture["origin"] + "/index.php/admin/extend/variables/edit/" + fixture["object_id"])
    else:
        if fixture["category"] == "target":
            row = page.locator(f"#smartplaylist_row_{fixture['object_id']}")
            context.click(row.locator('a[id^="edit_playlist_"]'))
            context.replace(page.locator('input[name=name]'), fixture["initial_name"] if wrong else fixture["expected"])
            submit = context.click(page.get_by_role("button", name="Cancel" if variant == "cancelled" else "Save", exact=True))
            context.navigate(fixture["origin"] + "/browse.php?action=smartplaylist")
        else:
            submit = context.click(page.locator("#savesearchbutton"))
            context.click(page.get_by_role("button", name="Continue", exact=True))
            context.navigate(fixture["origin"] + "/browse.php?action=smartplaylist")
    return submit


def make_label(context, fixture, group, submit_step):
    app, visible, variant = group["app"], context.visible, group["variant"]
    profile = "business_outcome_v1" if app in {"emlog", "anchor"} else "persistence"
    final_step = max(visible)
    requested = ({"Name": fixture["name"], "Sale Price": fixture["input_amount"]} if app == "akaunting" else
                 {"Nickname": fixture["expected"], "Username": "unchanged"} if app == "emlog" else
                 {"Name": fixture["name"], "Value": fixture["expected"]} if app == "anchor" else
                 {"Phone Number" if fixture["field"] == "phone_number" else "Mobile Number": fixture["expected"]}
                 if app == "appointments" else {"Name": "automatic name beginning with the signed-in username"
                 if fixture["category"] == "preservation" else fixture["expected"]})
    fields, steps, final_steps = [], {0, submit_step}, set()
    for field, target in requested.items():
        intent = ("preserve" if (app == "emlog" and field == "Username") or
                  (app == "anchor" and field == "Name" and fixture["category"] == "target") else
                  "generated" if app == "ampache" and fixture["category"] == "preservation" else "edit")
        before_step = next((step for step in (sorted(visible) if intent == "preserve" else sorted(visible, reverse=True))
                            if step < submit_step and visible[step]["fields"].get(field) is not None), None)
        before = None if before_step is None or intent == "generated" else {
            "value": visible[before_step]["fields"][field]["value"], "step": before_step}
        after_value = visible[final_step]["fields"].get(field)
        after = None if after_value is None else {"value": after_value["value"], "step": final_step}
        compared = before["value"] if intent == "preserve" and target == "unchanged" and before else target
        if app == "akaunting" and field == "Sale Price":
            matches = None if after is None else parse_usd(after["value"]) == parse_usd("$" + f"{float(fixture['expected']):,.2f}")
        elif intent == "generated":
            matches = None if after is None else after["value"].startswith(fixture["username"] + " - ")
        else:
            matches = None if after is None else after["value"] == compared
        fields.append({"field": field, "intent": intent, "requested_value": target,
            "before": before, "after": after,
            "input_matches_request": None if intent != "edit" or before is None else before["value"] == target,
            "result_matches_request": matches})
        if before is not None:
            steps.add(before["step"])
        if after is not None:
            final_steps.add(final_step)
    status, observation = submission_status(context, fixture, group, submit_step)
    inputs_established = all(field["input_matches_request"] is True for field in fields if field["intent"] == "edit")
    result_observed = bool(final_steps)
    if variant == "cancelled" or not inputs_established:
        verdict = "blocked"
    elif status == "rejected" and profile == "business_outcome_v1":
        verdict = "fail"
    elif status == "accepted" and all(field["after"] is not None for field in fields):
        verdict = "pass" if all(field["result_matches_request"] for field in fields) else "fail"
    else:
        verdict = "blocked"
    steps.update(final_steps)
    answer = {"action": "finish", "task_kind": "create" if app == "akaunting" or group["workflow"] == "preservation" and app in {"anchor", "ampache"} else "edit",
        "field_readings": fields, "submission": {"status": status, "evidence": {"step": submit_step, "observation": observation}},
        "result_observed": result_observed, "result_steps": sorted(final_steps), "expected_rejection": False,
        "verdict": verdict, "observation": observation + " Final fields are transcribed from the reopened screenshots.",
        "evidence_steps": sorted(steps)}
    if len(steps) > 5:
        raise ValueError(f"selected review evidence exceeds five screenshots: {sorted(steps)}")
    parse_review(json.dumps(answer), steps, profile)
    violation = review_guard(answer, profile)
    if violation:
        raise ValueError(violation)
    return answer, sorted(steps), profile


def submission_status(context, fixture, group, submit_step):
    snapshot = context.visible[submit_step]
    text = " ".join(snapshot["messages"])
    app = group["app"]
    if group["variant"] == "cancelled":
        return "unknown", "The edit was discarded or the form was left without submitting Save."
    if app == "emlog":
        if "Username has been set and cannot be modified" in text:
            return "rejected", "The valid nickname save visibly reports: " + text[:250]
        if context.observer.evidence.response_valid and any(word in text.casefold() for word in ("success", "saved")):
            return "accepted", "The profile Save visibly reports: " + text[:250]
    elif app == "anchor":
        if any(word in text.casefold() for word in ("already exists", "already exist", "name already")):
            return "rejected", "The ordinary variable edit visibly reports: " + text[:250]
        if any(word in text.casefold() for word in ("updated", "created", "saved")):
            return "accepted", "The variable form visibly reports: " + text[:250]
    elif app == "akaunting":
        if urlparse(snapshot["url"]).path == "/1/common/items" and snapshot["fields"]["Name"] is not None:
            return "accepted", "Save returns to the Items list and visibly identifies the newly named product."
    elif app == "appointments":
        if context.observer.evidence.response_valid and any(word in text.casefold() for word in ("saved", "success")):
            return "accepted", "The provider Save visibly reports: " + text[:250]
    elif app == "ampache":
        if fixture["category"] == "preservation" and "Your search has been saved" in snapshot["body_text"]:
            return "accepted", "The confirmation visibly states that the search was saved as a Smart Playlist."
        if (fixture["category"] == "target" and snapshot["playlist_dialog_open"] is False
                and snapshot["fields"]["Name"] is not None
                and not any(word in snapshot["body_text"] for word in ("TypeError", "Fatal error", "Uncaught"))):
            return "accepted", "The Save dialog closes and the Smart Playlists list shows the same playlist row."
    return "unknown", "The supplied submission screenshot does not establish acceptance or a task-specific business rejection."


def collect_one(browser, batch, group, build, port):
    job_id = f"{group['id']}-{build}-{group['variant']}"
    directory = batch / job_id
    attempt = 1
    while directory.exists():
        attempt += 1
        directory = batch / f"{job_id}-attempt-{attempt}"
    directory.mkdir()
    (directory / "private").mkdir()
    shutil.copyfile(Path(__file__), directory / "private/collector-source.py")
    fixture = json.loads((ROOT / "cases" / group["case_id"] / "fixtures.private.json").read_text())[group["workflow"]]
    fixture.update(copy.deepcopy(group["fixture"]))
    instance_type, authenticate, observer_type = APPS[group["app"]]
    instance = instance_type(ROOT, directory / "private/instance", build, port)
    fixture["origin"] = instance.url
    result = {"run_id": directory.name, "job_id": job_id, "case_id": group["case_id"],
        "build": build, "workflow": group["workflow"], "scenario_id": group["id"],
        "parent_scenario_id": group["parent_scenario_id"], "data_split": group["split"],
        "calibration": group["calibration"], "variant": group["variant"], "actions": 0}
    started = time.monotonic()
    context = None
    try:
        instance.start()
        instance.seed_test_fixture(fixture)
        with browser.new_page(viewport=VIEWPORT, device_scale_factor=1, locale="en-US", timezone_id="UTC") as page:
            page.set_default_timeout(10000)
            authenticate(page, instance.url, fixture)
            observer = observer_type(page, fixture)
            context = TestContext(directory, result, observer, page, group["app"], fixture)
            context.capture("Authenticated initial fixture")
            submit_step = execute_test_flow(context, fixture, group)
            oracle_verdict, reason = judge(observer.evidence)
            write_json(directory / "private/oracle.json", {**observer.data(), "oracle_verdict": oracle_verdict, "oracle_reason": reason})
            answer, steps, profile = make_label(context, fixture, group, submit_step)
            if answer["verdict"] != "blocked" and answer["verdict"] != oracle_verdict:
                raise ValueError(f"visible review {answer['verdict']} contradicts independent oracle {oracle_verdict}: {reason}")
            frames = [{"step": step, "roles": ["same_run_public_evidence"]} for step in steps]
            paths = {step: str((directory / f"screenshots/{step:03d}.png").relative_to(batch)) for step in steps}
            source_names = ["trace.jsonl", "private/oracle.json", "private/visible.json", "private/instance/instance.json", "private/collector-source.py",
                            *[f"screenshots/{step:03d}.png" for step in steps]]
            metadata = scenario_metadata(group["case_id"], group["workflow"], group["task"], group["initial"],
                                         group["requested_changes"], group["parent_scenario_id"])
            row = {"id": directory.name, "phase": "review", "sample_kind": "final_test_review",
                "case_id": group["case_id"], "build": build, "workflow": group["workflow"],
                "data_split": group["split"], "scenario_id": group["id"], "calibration": group["calibration"],
                **metadata, "review_format": "evidence_v2", "review_profile": profile,
                "variant": group["variant"], "label_source": "same_run_visible_fields_and_independent_oracle",
                "run_directory": str(directory), "screenshot_steps": steps, "review_frames": frames,
                "derived_provenance": {"source_run_id": directory.name,
                    "source_trace_sha256": file_hash(directory / "trace.jsonl"), "screenshot_steps": steps,
                    "deployment_manifest": str(directory / "private/instance/instance.json"),
                    "deployment_manifest_sha256": file_hash(directory / "private/instance/instance.json"),
                    "human_pixel_review": "pending"},
                "messages": [{"role": "system", "content": [{"type": "text", "text": review_prompt(profile)}]},
                    {"role": "user", "content": review_content(group["task"], context.history, frames, paths)},
                    {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)}]}
            write_json(directory / "label.json", answer)
            write_json(directory / "public.json", {"task": group["task"]})
            result.update(verdict=answer["verdict"], oracle_verdict=oracle_verdict, oracle_reason=reason,
                review_profile=profile, submission_status=answer["submission"]["status"], screenshot_steps=steps,
                source_hashes={name: file_hash(directory / name) for name in source_names})
    except Exception:
        write_json(directory / "result.json", {**result, "complete": False})
        (directory / "private/error.txt").write_text(traceback.format_exc())
        if context is not None:
            write_json(directory / "private/oracle.json", context.observer.data())
        raise
    finally:
        instance.close()
    result.update(complete=True, elapsed_seconds=time.monotonic() - started)
    write_json(directory / "result.json", result)
    return row, result


def main():
    args = arguments()
    catalog = yaml.safe_load(args.catalog.read_text())
    jobs = [(group, build) for group in catalog["groups"]
            if group["app"] in args.app and group["calibration"] == args.calibration
            and (not args.group or group["id"] in args.group) for build in group["builds"]]
    if args.limit:
        jobs = jobs[:args.limit]
    if args.output.exists():
        manifest = json.loads((args.output / "manifest.json").read_text())
    else:
        args.output.mkdir(parents=True)
        shutil.copyfile(args.catalog, args.output / "catalog.yaml")
        shutil.copyfile(Path(__file__), args.output / "collector-source.py")
        manifest = {"complete": False, "calibration": args.calibration, "teacher_model_calls": 0,
            "samples_planned": len(jobs), "catalog_sha256": file_hash(args.catalog), "completed": []}
    manifest["samples_planned"] = len(jobs)
    manifest["complete"] = False
    completed = {result["job_id"] for result in manifest["completed"]}
    write_json(args.output / "manifest.json", manifest)
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            for group, build in jobs:
                job_id = f"{group['id']}-{build}-{group['variant']}"
                if job_id in completed:
                    continue
                row, result = collect_one(browser, args.output, group, build, args.port)
                with (args.output / "reviews.jsonl").open("a") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                manifest["completed"].append(result)
                write_json(args.output / "manifest.json", manifest)
                print(json.dumps(result, ensure_ascii=False), flush=True)
    manifest["complete"] = len(manifest["completed"]) == len(jobs)
    manifest["dataset_sha256"] = {"reviews.jsonl": file_hash(args.output / "reviews.jsonl")}
    write_json(args.output / "manifest.json", manifest)


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "configs/review-final-test-expanded-v1.yaml")
    parser.add_argument("--app", choices=APPS, action="append", required=True)
    parser.add_argument("--calibration", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--group", action="append")
    args = parser.parse_args()
    args.catalog, args.output = args.catalog.resolve(), args.output.resolve()
    return args


if __name__ == "__main__":
    main()
