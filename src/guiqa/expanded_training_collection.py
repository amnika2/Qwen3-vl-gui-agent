"""Collect new typed reviews from the five existing form and text applications."""

import argparse
import copy
import hashlib
import json
import re
import shutil
import socket
import time
import traceback
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import yaml
from playwright.sync_api import sync_playwright

from .apps.impress_opencart import ImpresscmsInstance, ImpressObserver, impress_login, impress_oracle
from .apps.litecart_wolf import LitecartInstance, LitecartObserver, litecart_flow, litecart_login
from .apps.mantis_phpipam import MantisInstance, MantisObserver, mantis_flow, mantis_login, mantis_oracle
from .artifacts import write_json
from .browser import VIEWPORT
from .oracle import judge
from .review_context import review_content
from .review_contract import parse_review, review_guard, review_prompt
from .review_scenarios import scenario_metadata
from .rl_targeted_collection import visible_reading
from .runner import RunContext
from .sft_data import replay_flow
from .sft_flows import SftFlow, click_at, press, scroll_to, type_text
from .sft_scenarios import SupplementaryObserver, prepare_scenario, scenario_flow, scenario_task
from .training_cases import dokuwiki_login, flatpress_login
from .training_instances import DokuwikiInstance, FlatpressInstance


ROOT = Path(__file__).resolve().parents[2]
COLLECTOR_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
APPS = {
    "mantis": ("sweeper-107", MantisInstance, mantis_login, MantisObserver, mantis_oracle),
    "impress": ("sweeper-084", ImpresscmsInstance, impress_login, ImpressObserver, impress_oracle),
    "litecart": ("sweeper-105", LitecartInstance, litecart_login, LitecartObserver, lambda observer: judge(observer.evidence)),
    "dokuwiki": ("sweeper-034", DokuwikiInstance, dokuwiki_login, SupplementaryObserver, lambda observer: judge(observer.evidence)),
    "flatpress": ("sweeper-053", FlatpressInstance, flatpress_login, SupplementaryObserver, lambda observer: judge(observer.evidence)),
}
LABELS = {"sitename": "Site name", "slogan": "Slogan for your site", "adminmail": "Admin mail address",
          "code": "Code", "name": "Name", "description": "Description", "content": "Content", "subject": "Subject",
          "country": "Saved country rows", "city": "Saved city rows",
          "version": "Product Version", "category": "Category", "summary": "Summary",
          "steps_to_reproduce": "Steps To Reproduce"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "configs/review-expansion-extended-v1.yaml")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, choices=(13424, 13425, 13426, 13427), required=True)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    groups = load_catalog(args.catalog)
    jobs = [(group, build) for group in groups for build in group["builds"]]
    jobs = [job for index, job in enumerate(jobs) if index % args.shards == args.shard_index]
    batch, manifest = prepare_batch(args, jobs)
    done = {result["run_id"] for result in manifest["completed"]}
    pending = [(group, build) for group, build in jobs if run_name(group, build) not in done]
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            for group, build in pending[:args.limit]:
                row, result = collect_one(browser, batch, group, build, args.port)
                for name in ("reviews", group["data_split"]):
                    with (batch / f"{name}.jsonl").open("a") as handle:
                        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                manifest["completed"].append(result)
                write_json(batch / "manifest.json", manifest)
                print(json.dumps(result, ensure_ascii=False), flush=True)
    manifest["complete"] = len(manifest["completed"]) == len(jobs)
    manifest["labels"] = dict(Counter(result["verdict"] for result in manifest["completed"]))
    manifest["dataset_sha256"] = {path.name: file_hash(path) for path in batch.glob("*.jsonl")}
    write_json(batch / "manifest.json", manifest)
    print(json.dumps({"batch": str(batch), "complete": manifest["complete"], "samples": len(manifest["completed"])}), flush=True)


def load_catalog(path):
    groups = yaml.safe_load(path.read_text())["groups"]
    parents, partitions, keys = set(), {}, {}
    split = yaml.safe_load((ROOT / "configs/sft-split.yaml").read_text())
    allowed = set(split["training_pilot"] + split["training_supplementary"])
    for group in groups:
        app = group["app"]
        if app not in APPS or APPS[app][0] not in allowed:
            raise ValueError("extended collection requires an existing training application")
        if group["data_split"] not in {"train", "validation"} or set(group["builds"]) - {"A", "B"}:
            raise ValueError("invalid split or build")
        parent = group["parent_scenario_id"]
        if parent in parents:
            raise ValueError("catalog parent IDs must be unique")
        parents.add(parent)
        fixture, task, initial, requested = prepare_task(group)
        metadata, _ = scenario_identity(group, fixture, task, initial, requested)
        key = metadata["content_key"]
        if key in keys:
            raise ValueError("catalog repeats business content")
        keys[key], partitions[parent] = group["data_split"], group["data_split"]
    return groups


def prepare_batch(args, jobs):
    batch = args.output.resolve()
    batch.mkdir(parents=True, exist_ok=True)
    path = batch / "manifest.json"
    if path.exists():
        manifest = json.loads(path.read_text())
        if manifest["catalog_sha256"] != file_hash(args.catalog) or manifest["samples_planned"] != len(jobs):
            raise ValueError("resumed catalog or job assignment differs")
        if COLLECTOR_SHA256 != manifest["source_sha256"]["src/guiqa/expanded_training_collection.py"]:
            target = batch / "collector-revisions" / (COLLECTOR_SHA256 + ".py")
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(Path(__file__), target)
        return batch, manifest
    shutil.copy2(args.catalog, batch / "catalog.yaml")
    sources = ("src/guiqa/expanded_training_collection.py", "src/guiqa/review_scenarios.py",
               "src/guiqa/apps/mantis_phpipam.py", "src/guiqa/apps/impress_opencart.py",
               "src/guiqa/apps/litecart_wolf.py", "src/guiqa/training_instances.py",
               "src/guiqa/training_cases.py", "src/guiqa/sft_scenarios.py", "src/guiqa/runner.py",
               "src/guiqa/sft_data.py", "src/guiqa/browser.py", "src/guiqa/oracle.py")
    for name in sources:
        target = batch / "source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    manifest = {"complete": False, "calibration": False, "samples_planned": len(jobs),
                "teacher_model_calls": 0, "training_started": False, "host": socket.gethostname(),
                "catalog_sha256": file_hash(args.catalog), "source_sha256": {name: file_hash(ROOT / name) for name in sources},
                "jobs": [{"run_id": run_name(group, build), "parent_scenario_id": group["parent_scenario_id"],
                          "data_split": group["data_split"]} for group, build in jobs], "completed": []}
    write_json(path, manifest)
    return batch, manifest


def run_name(group, build):
    return group["parent_scenario_id"] + "-" + build + "-saved"


def scenario_identity(group, fixture, task, initial, requested):
    identity_task = (task.replace(fixture["page_id"], "<scenario-page>")
                     if group["app"] in {"dokuwiki", "flatpress"} else task)
    metadata = scenario_metadata(APPS[group["app"]][0], group["workflow"], identity_task, initial, requested,
                                 group["parent_scenario_id"])
    return metadata, identity_task


def prepare_task(group):
    app = group["app"]
    case = APPS[app][0]
    fixture = json.loads((ROOT / "cases" / case / "fixtures.private.json").read_text())["target"]
    if app in {"dokuwiki", "flatpress"}:
        entry = {**group["scenario"], "id": group["parent_scenario_id"], "case_id": case,
                 "build": group["builds"][0], "workflow": group["workflow"], "data_split": group["data_split"]}
        scenario = prepare_scenario(entry)
        fixture.update(scenario["fixture"])
        initial = {"content": fixture["initial"]}
        if app == "flatpress":
            initial["subject"] = fixture["initial_subject"]
        requested = fixture["stages"][-1]
        task = scenario["public"]["task"]
        if app == "dokuwiki":
            review_fixture = {key: value for key, value in fixture.items() if key not in {"summary", "minor"}}
            task = scenario_task(review_fixture)
        if app == "flatpress" and "subject" not in entry:
            task += f" Keep Subject as {fixture['initial_subject']!r}."
        if fixture["edit"] in {"append", "replace_suffix", "keep"}:
            task = ("Initial Content before this task is " + json.dumps(fixture["initial"], ensure_ascii=False)
                    + ". " + task)
        return fixture, task, initial, requested
    fixture.update(copy.deepcopy(group["fixture"]))
    if app == "mantis":
        fixture["expected"] = fixture["summary"]
        requested = {"category": "General", "version": fixture["version"], "summary": fixture["summary"],
                     "description": fixture["description"]}
        if "steps_to_reproduce" in fixture:
            requested["steps_to_reproduce"] = fixture["steps_to_reproduce"]
        initial = {"category": "", "version": "", "summary": "", "description": ""}
        task = "In MantisBT, report a new issue in Softfield Tracker. " + "; ".join(
            f"Set {LABELS[key]} to {value!r}" for key, value in requested.items())
        task += ". Submit the report, then reload the same issue and inspect the saved fields. A valid report must be accepted; an application error is an unexpected failure."
    elif app == "impress":
        requested, initial = fixture["values"], fixture["initial"]
        fixture["expected"] = json.dumps(requested, sort_keys=True)
        task = "In ImpressCMS General Settings, " + "; ".join(f"set {LABELS[key]} to {value!r}" for key, value in requested.items())
        task += ". Save, reopen General Settings and inspect those same fields. A PHP/internal error is an unexpected failure of this valid save."
    else:
        initial = fixture.get("initial", {"code": "", "name": "", "description": ""})
        fixture["country_index"] = {"AF": 1, "AL": 3, "DZ": 4, "AS": 5, "AD": 6}[fixture["country_code"]]
        requested = {key: fixture[key] for key in ("code", "name", "description")}
        fixture["expected"] = json.dumps({**requested, "countries": [fixture["country"]],
                                            "cities": [fixture["city"] or "-- All Cities --"]}, sort_keys=True)
        task = "In LiteCart Geo Zones, " + (f"edit {initial['name']!r}" if fixture.get("initial") else "create a new geo zone")
        task += ". " + "; ".join(f"Set {LABELS[key]} to {value!r}" for key, value in requested.items())
        task += f". In the pending Country/City row choose {fixture['country']!r} and "
        task += (f"type City {fixture['city']!r}" if fixture["city"] else "leave City visibly empty to apply to all cities")
        task += ". Click Save directly, reopen the same geo zone and inspect both its fields and saved Country/City table."
    task += " Report pass when the visible completed result matches, fail for an unexpected rejection or saved mismatch, or blocked when the required evidence is unavailable."
    if app == "litecart":
        requested = {**requested, "country": fixture["country"], "city": fixture["city"] or "-- All Cities --"}
    return fixture, task, initial, requested


def make_flow(app, fixture, build):
    if app == "mantis":
        flow = mantis_flow(fixture, build)
        steps = flow.steps[:flow.submit_step - 2] + [scroll_to("#category_id")]
        steps += flow.steps[flow.submit_step - 2:]
        submit = flow.submit_step + 1
        def actions():
            yield from steps
            if urlparse(page_ref[0].url).path == "/view.php":
                for field in ("category", "product-version", "summary", "description", "steps-to-reproduce"):
                    if field != "steps-to-reproduce" or "steps_to_reproduce" in fixture:
                        yield scroll_to("td.bug-" + field)
        page_ref = []
        first = steps[0]
        steps[0] = lambda page: (page_ref.append(page), first(page))[1]
        return SftFlow(actions(), submit - 2, submit, navigation_steps=())
    if app == "impress":
        steps = []
        if fixture.get("start") == "list":
            steps.append(click_at('a[title="Edit General Settings"]'))
        for field in fixture["field_order"]:
            steps += [scroll_to(f"input[name={field}]"), click_at(f"input[name={field}]"),
                      press("Control+a"), type_text(fixture["values"][field])]
        for field in fixture["values"]:
            steps.append(scroll_to(f"input[name={field}]"))
        input_step = len(steps)
        steps += [scroll_to("#button"), click_at("#button")]
        submit = len(steps)
        page_ref = []
        first = steps[0]
        steps[0] = lambda page: (page_ref.append(page), first(page))[1]
        def actions():
            yield from steps
            if "getConfig() on null" not in page_ref[0].locator("body").inner_text():
                yield click_at('a[title="Edit General Settings"]')
                for field in fixture["values"]:
                    yield scroll_to(f"input[name={field}]")
        return SftFlow(actions(), input_step, submit)
    if app == "litecart":
        flow = litecart_flow(fixture, build)
        steps = flow.steps[:flow.input_step]
        for selector in ('input[name="code"]', 'input[name="new_zone[city]"]'):
            steps.append(scroll_to(selector))
        input_step = len(steps)
        steps += flow.steps[flow.input_step:]
        submit = input_step + 1
        steps += [scroll_to('input[name="code"]'), scroll_to("table.data-table")]
        return SftFlow(steps, input_step, submit)
    flow = scenario_flow(fixture)
    reads = [scroll_to(fixture["selector"])]
    if app == "flatpress":
        reads.append(scroll_to("#subject"))
    steps = flow.steps[:flow.input_step] + reads + flow.steps[flow.input_step:]
    return SftFlow(steps, flow.input_step + len(reads), flow.submit_step + len(reads),
                   navigation_steps=tuple(step + len(reads) if step > flow.input_step else step
                                          for step in flow.navigation_steps))


class ExpandedContext(RunContext):
    def __init__(self, directory, result, observer, page, app, fixture):
        super().__init__(directory, result, observer, page)
        self.app, self.fixture, self.visible = app, fixture, {}

    def capture(self, description, action=None, raw=None, elapsed=0, error=None):
        path = super().capture(description, action, raw, elapsed, error)
        fields = visible_fields(self.page, self.app, self.fixture)
        body = self.page.locator("body").inner_text()
        self.visible[self.result["actions"]] = {"fields": fields, "url": self.page.url,
            "application_error": ("APPLICATION ERROR #29" in body or "getConfig() on null" in body),
            "body_excerpt": body[:3500]}
        write_json(self.directory / "private/visible.json", self.visible)
        return path


def visible_fields(page, app, fixture):
    if app == "mantis":
        names = ["category", "version", "summary", "description"]
        if "steps_to_reproduce" in fixture:
            names.append("steps_to_reproduce")
        report = urlparse(page.url).path == "/bug_report_page.php"
        selectors = {"category": "#category_id", "version": "#product_version", "summary": "input[name=summary]",
                     "description": "textarea[name=description]", "steps_to_reproduce": "textarea[name=steps_to_reproduce]"}
        final = {"category": "td.bug-category", "version": "td.bug-product-version", "summary": "td.bug-summary",
                 "description": "td.bug-description", "steps_to_reproduce": "td.bug-steps-to-reproduce"}
        values = {LABELS[key]: visible_reading(page.locator((selectors if report else final)[key])) for key in names}
        if report:
            for key in ("category", "version"):
                if values[LABELS[key]] is not None:
                    values[LABELS[key]]["value"] = page.locator(selectors[key] + " option:checked").inner_text().strip()
        elif values["Summary"] is not None:
            values["Summary"]["value"] = re.sub(r"^\d+:\s*", "", values["Summary"]["value"])
        return values
    if app == "impress":
        return {LABELS[field]: visible_reading(page.locator(f"input[name={field}]")) for field in fixture["values"]}
    if app == "litecart":
        values = {LABELS[field]: visible_reading(page.locator(f'input[name="{field}"]')) for field in ("code", "name", "description")}
        values["Saved country rows input"] = visible_reading(page.locator('select[name="new_zone[country_code]"]'))
        if values["Saved country rows input"] is not None:
            values["Saved country rows input"]["value"] = page.locator('select[name="new_zone[country_code]"] option:checked').inner_text().strip()
        values["Saved city rows input"] = visible_reading(page.locator('input[name="new_zone[city]"]'))
        table = page.locator("table.data-table")
        table_reading = visible_reading(table)
        rows = table.locator("tbody tr")
        for name, column in (("Saved country rows", 2), ("Saved city rows", 4)):
            values[name] = (visible_reading(rows.first.locator("td").nth(column - 1)) if rows.count()
                            else {**table_reading, "value": "", "basis": "visible_saved_table_has_no_rows"} if table_reading else None)
        return values
    values = {"Content": visible_reading(page.locator(fixture["selector"]))}
    if app == "flatpress":
        values["Subject"] = visible_reading(page.locator("#subject"))
    return values


def collect_one(browser, batch, group, build, port):
    app = group["app"]
    case, instance_type, login, observer_type, oracle = APPS[app]
    name = run_name(group, build)
    directory = batch / name
    directory.mkdir()
    (directory / "private").mkdir()
    fixture, task, initial, requested = prepare_task(group)
    metadata, identity_task = scenario_identity(group, fixture, task, initial, requested)
    profile = "business_outcome_v1" if app in {"mantis", "impress"} else "persistence"
    result = {"run_id": name, "case_id": case, "build": build, "workflow": group["workflow"],
              "scenario_id": group["parent_scenario_id"], "data_split": group["data_split"],
              "actions": 0, "variant": "saved", "collector_sha256": COLLECTOR_SHA256, **metadata}
    write_json(directory / "private/scenario.json", {"group": group, "fixture": fixture, "task": task})
    instance = instance_type(ROOT, directory / "private/instance", build, port)
    started = time.monotonic()
    try:
        instance.start()
        instance.seed_training_page(fixture)
        with browser.new_page(viewport=VIEWPORT, device_scale_factor=1, locale="en-US", timezone_id="UTC") as page:
            page.set_default_timeout(10000)
            login(page, instance.url, fixture)
            observer = observer_type(page, fixture)
            context = ExpandedContext(directory, result, observer, page, app, fixture)
            context.capture("Authenticated initial view")
            flow = make_flow(app, fixture, build)
            replay_flow(context, flow)
            verdict, reason = oracle(observer)
            write_json(directory / "private/oracle.json", observer.data())
            label, steps = make_label(context, group, fixture, requested, flow.submit_step, verdict)
            parse_review(json.dumps(label), set(steps), profile)
            violation = review_guard(label, profile)
            if violation:
                raise ValueError(violation)
            if label["verdict"] != verdict and verdict in {"pass", "fail"}:
                raise ValueError(f"visible review {label['verdict']} differs from same-run oracle {verdict}")
            write_json(directory / "label.json", label)
            write_json(directory / "public.json", {"task": task})
            frames = [{"step": step, "roles": ["same_run_visible_candidate"]} for step in steps]
            paths = {step: str((directory / f"screenshots/{step:03d}.png").relative_to(batch)) for step in steps}
            row = {"id": name, "source_run_id": name, "step": result["actions"], "phase": "review",
                   "sample_kind": "review_curriculum", "case_id": case, "workflow": group["workflow"],
                   "build": build, "data_split": group["data_split"], "scenario_id": group["parent_scenario_id"],
                   "calibration": False, **metadata, "review_format": "evidence_v2", "review_profile": profile,
                   "identity_task": identity_task,
                   "variant": "saved", "label_source": "same_run_visible_fields_and_independent_oracle",
                   "run_directory": str(directory), "screenshot_steps": steps, "review_frames": frames,
                   "derived_provenance": {"source_run_id": name, "source_trace_sha256": file_hash(directory / "trace.jsonl"),
                       "screenshot_steps": steps, "human_pixel_review": "first_sample_checked_then_typed_collection",
                       "deployment_manifest": str(directory / "private/instance/instance.json"),
                       "deployment_manifest_sha256": file_hash(directory / "private/instance/instance.json")},
                   "messages": [{"role": "system", "content": [{"type": "text", "text": review_prompt(profile)}]},
                                {"role": "user", "content": review_content(task, context.history, frames, paths)},
                                {"role": "assistant", "content": json.dumps(label, ensure_ascii=False)}]}
            result.update(complete=True, verdict=label["verdict"], oracle_verdict=verdict, oracle_reason=reason,
                          screenshot_steps=steps, source_hashes={file: file_hash(directory / file) for file in
                              ["trace.jsonl", "private/oracle.json", "private/visible.json", "private/instance/instance.json",
                               *[f"screenshots/{step:03d}.png" for step in steps]]})
    except Exception:
        (directory / "private/error.txt").write_text(traceback.format_exc())
        write_json(directory / "result.json", {**result, "complete": False})
        raise
    finally:
        instance.close()
    result["elapsed_seconds"] = time.monotonic() - started
    write_json(directory / "result.json", result)
    return row, result


def make_label(context, group, fixture, requested, submit, oracle_verdict):
    app, visible = group["app"], context.visible
    submitted = [event for event in context.observer.evidence.events if event["kind"] == "submit_response"]
    submit = submitted[-1]["step"] if submitted else submit
    rejected = visible[submit]["application_error"]
    status = "rejected" if rejected else "accepted" if submitted and submitted[-1].get("accepted") else "unknown"
    names = {LABELS.get(key, key.title()): value for key, value in requested.items()}
    last_type = max(entry["step"] for entry in context.history if entry["step"] < submit
                    and entry["action"] and entry["action"]["action"] == "type")
    required = {("before", field) for field in names}
    if status == "accepted":
        required |= {("after", field) for field in names}
    candidates = {}
    for step, snapshot in visible.items():
        phase = "before" if last_type <= step < submit else "after" if step > submit else None
        if phase is None:
            continue
        covered = set()
        for field in names:
            key = field + " input" if app == "litecart" and phase == "before" and field in {"Saved country rows", "Saved city rows"} else field
            if snapshot["fields"].get(key) is not None:
                covered.add((phase, field))
        if covered:
            candidates[step] = covered
    selected, remaining = {submit}, set(required)
    while remaining and len(selected) < 5:
        step = max(candidates, key=lambda value: (len(candidates[value] & remaining), value))
        covered = candidates[step] & remaining
        if not covered:
            break
        selected.add(step)
        remaining -= covered
    fields, result_steps = [], set()
    for field, target in names.items():
        readings = {}
        for phase in ("before", "after"):
            key = field + " input" if app == "litecart" and phase == "before" and field in {"Saved country rows", "Saved city rows"} else field
            steps = [step for step in selected if (phase, field) in candidates.get(step, set())]
            readings[phase] = ({"value": visible[max(steps)]["fields"][key]["value"], "step": max(steps)} if steps else None)
            if phase == "after" and steps:
                result_steps.add(max(steps))
        intent = "preserve" if app == "flatpress" and ((field == "Subject" and "subject" not in group["scenario"])
                  or (field == "Content" and fixture["edit"] == "keep")) else "edit"
        before_match = readings["before"] is not None and readings["before"]["value"] == target
        if app == "mantis" and field == "Category" and readings["before"] is not None:
            before_match = readings["before"]["value"] in {target, "[All Projects] " + target}
        if app == "litecart" and field == "Saved city rows" and target == "-- All Cities --":
            before_match = readings["before"] is not None and readings["before"]["value"] == ""
        fields.append({"field": field, "intent": intent, "requested_value": target,
                       **readings, "input_matches_request": before_match if intent == "edit" else None,
                       "result_matches_request": (readings["after"]["value"] == target if readings["after"] is not None else None)})
    entered = all(field["input_matches_request"] is True for field in fields if field["intent"] == "edit")
    if not entered or status == "unknown":
        verdict = "blocked"
    elif status == "rejected":
        verdict = "fail"
    elif any(field["after"] is None for field in fields):
        verdict = "blocked"
    else:
        verdict = "pass" if all(field["result_matches_request"] for field in fields) else "fail"
    outcome = ("The submission displays APPLICATION ERROR #29: Invalid value for version." if app == "mantis" and rejected
               else "The submission displays the PHP/internal error getConfig() on null." if rejected
               else "The completed submission returns to the application's visible saved-record/settings page." if status == "accepted"
               else "No accepted submission is established.")
    return {"action": "finish", "task_kind": "create" if app == "mantis" or app == "litecart" and not fixture.get("initial") else "edit",
            "field_readings": fields, "submission": {"status": status, "evidence": {"step": submit, "observation": outcome}},
            "result_observed": bool(result_steps), "result_steps": sorted(result_steps), "expected_rejection": False,
            "verdict": verdict, "observation": outcome,
            "evidence_steps": sorted(selected)}, sorted(selected)


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def derive_review_tasks(batch):
    batch = Path(batch).resolve()
    source_copy = batch / "collector-revisions" / (COLLECTOR_SHA256 + ".py")
    source_copy.parent.mkdir(exist_ok=True)
    shutil.copy2(Path(__file__), source_copy)
    source = batch / "reviews.jsonl"
    source_sha256 = file_hash(source)
    rows = [json.loads(line) for line in source.read_text().splitlines()]
    derived, changes = [], []
    for original in rows:
        row = copy.deepcopy(original)
        directory = Path(row["run_directory"])
        recorded = json.loads((directory / "private/scenario.json").read_text())
        group = recorded["group"]
        if group["app"] in {"dokuwiki", "flatpress"}:
            fixture, task, initial, requested = prepare_task(group)
            if task != recorded["task"]:
                metadata, identity_task = scenario_identity(group, fixture, task, initial, requested)
                trace = [json.loads(line) for line in (directory / "trace.jsonl").read_text().splitlines()]
                history = [{"step": item["step"], "action": item["action"],
                            "result": item["execution_error"] or "executed"} for item in trace]
                images = [item["image"] for message in row["messages"][:-1] for item in message["content"] if item["type"] == "image"]
                paths = {frame["step"]: path for frame, path in zip(row["review_frames"], images)}
                change = {"source_row_id": original["id"], "source_run_id": row["derived_provenance"]["source_run_id"],
                          "original_public_task": recorded["task"], "review_task": task,
                          "original_public_task_sha256": hashlib.sha256(recorded["task"].encode()).hexdigest(),
                          "review_task_sha256": hashlib.sha256(task.encode()).hexdigest(),
                          "scope": "Review persisted Content/Subject; give seeded initial Content for append/suffix/keep; retain summary/minor as ancillary actions",
                          "source_review_sha256": source_sha256, "derivation_source_sha256": COLLECTOR_SHA256}
                row.update(metadata)
                row["id"] += ":content-persistence-review"
                row["identity_task"] = identity_task
                row["messages"][1]["content"] = review_content(task, history, row["review_frames"], paths)
                row["derived_provenance"]["task_derivation"] = change
                changes.append(change)
        derived.append(row)
    output = batch / "reviews-content-scope.jsonl"
    output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in derived))
    record = {"samples": len(derived), "source": str(source), "source_sha256": source_sha256,
              "output": str(output), "output_sha256": file_hash(output), "original_runs_unchanged": True,
              "labels_images_actions_unchanged": True, "task_derivations": changes}
    write_json(batch / "review-task-derivations.json", record)
    return record


if __name__ == "__main__":
    main()
