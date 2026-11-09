"""Collect paired, real UI evidence for the review RL curriculum."""

import argparse
import copy
import hashlib
import json
import shutil
import tarfile
import time
import traceback
import uuid
from decimal import Decimal
from pathlib import Path

import yaml
from playwright.sync_api import sync_playwright

from .apps.impress_opencart import (OpencartInstance, OpencartObserver, opencart_flow,
                                   opencart_login, option_row)
from .apps.mantis_phpipam import (PhpipamInstance, PhpipamObserver, phpipam_flow,
                                 phpipam_login)
from .apps.litecart_wolf import WolfInstance, WolfObserver, wolf_flow, wolf_login, wolf_oracle
from .artifacts import write_json
from .browser import VIEWPORT
from .oracle import in_view, judge
from .review_context import review_content
from .review_contract import parse_review, review_guard, review_prompt
from .review_scenarios import scenario_metadata
from .runner import RunContext
from .sft_data import replay_flow
from .sft_flows import SftFlow, click_at, press, scroll_to, typesetter_text
from .training_cases import TypesetterObserver, typesetter_login
from .training_instances import TypesetterInstance

ROOT = Path(__file__).resolve().parents[2]
APPS = {
    "phpipam": ("sweeper-142", PhpipamInstance, phpipam_login, PhpipamObserver, phpipam_flow),
    "opencart": ("sweeper-125", OpencartInstance, opencart_login, OpencartObserver, opencart_flow),
    "typesetter": ("sweeper-185", TypesetterInstance, typesetter_login, TypesetterObserver, typesetter_text),
    "wolf": ("sweeper-195", WolfInstance, wolf_login, WolfObserver, wolf_flow),
}


def main():
    args = arguments()
    catalog = load_catalog(args.catalog)
    batch = (args.output or ROOT / "runs" / ("rl-targeted-" + uuid.uuid4().hex[:8])).resolve()
    jobs = shard_jobs(collection_jobs(catalog, args), args.shard_index, args.shards)
    if args.resume:
        manifest = json.loads((batch / "manifest.json").read_text())
        if manifest["catalog_sha256"] != file_hash(args.catalog) or manifest["jobs"] != json.loads(json.dumps(jobs)):
            raise ValueError("resume must use the frozen catalog and original job list")
        manifest.setdefault("collector_source_snapshots", []).append(snapshot_source(batch, "-resume-" + str(time.time_ns())))
    else:
        batch.mkdir(parents=True, exist_ok=False)
        shutil.copy2(args.catalog, batch / "catalog.yaml")
        source_snapshot = snapshot_source(batch)
        manifest = {"calibration": args.calibration, "runs_planned": len(jobs),
                    "samples_planned": sum(build in group.get("retain_builds", ["A", "B"]) for group, build, _ in jobs),
                    "training_applications_only": True, "teacher_model_calls": 0,
                    "catalog_sha256": file_hash(args.catalog), "jobs": jobs, "completed": [],
                    "retained_review_ids": [], "failures": [], "collector_source_snapshots": [source_snapshot]}
    write_json(batch / "manifest.json", manifest)
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            for group, build, variant in jobs:
                job_id = f"{group['app']}-{group['id']}-{build}-{variant}"
                if any(record.get("job_id", record["run_id"]) == job_id for record in manifest["completed"]):
                    continue
                attempt, name = 1, job_id
                while (batch / name).exists():
                    attempt += 1
                    name = f"{job_id}-attempt-{attempt}"
                try:
                    row, result = collect_one(browser, batch, group, build, variant, args.port, name)
                except Exception as error:
                    manifest.setdefault("failures", []).append({"job_id": job_id, "run_id": name,
                                                               "error": f"{type(error).__name__}: {error}"})
                    write_json(batch / "manifest.json", manifest)
                    print(json.dumps(manifest["failures"][-1]), flush=True)
                    if not manifest["completed"]:
                        manifest["complete"] = False
                        write_json(batch / "manifest.json", manifest)
                        raise SystemExit(4)
                    continue
                result["job_id"] = job_id
                write_json(batch / name / "result.json", result)
                with (batch / "all-reviews.jsonl").open("a") as output:
                    output.write(json.dumps(row, ensure_ascii=False) + "\n")
                if build in group.get("retain_builds", ["A", "B"]):
                    for filename in ("reviews.jsonl", f"{group['split']}.jsonl"):
                        with (batch / filename).open("a") as output:
                            output.write(json.dumps(row, ensure_ascii=False) + "\n")
                    manifest["retained_review_ids"].append(row["id"])
                manifest["completed"].append(result)
                write_json(batch / "manifest.json", manifest)
                print(json.dumps(result, ensure_ascii=False), flush=True)
    manifest["complete"] = len(manifest["completed"]) == len(jobs)
    manifest["completed_runs"] = len(manifest["completed"])
    manifest["review_samples"] = len(manifest["retained_review_ids"])
    manifest["dataset_sha256"] = {name: file_hash(batch / name) for name in
                                  ("reviews.jsonl", "all-reviews.jsonl", "train.jsonl", "validation.jsonl") if (batch / name).exists()}
    write_json(batch / "manifest.json", manifest)
    print(json.dumps({"batch": str(batch), "samples": len(jobs), "calibration": args.calibration}), flush=True)
    if not manifest["complete"]:
        raise SystemExit(4)


def collect_one(browser, batch, group, build, variant, port, name=None):
    case, instance_type, login, observer_type, make_flow = APPS[group["app"]]
    name = name or f"{group['app']}-{group['id']}-{build}-{variant}"
    directory = batch / name
    directory.mkdir()
    fixture, task = task_fixture(group)
    identity = task_identity(group, fixture, task)
    profile = "business_outcome_v1" if group["app"] == "wolf" else "persistence"
    instance = instance_type(ROOT, directory / "private/instance", build, port)
    result = {"run_id": name, "case_id": case, "workflow": "target", "actions": 0,
              "build": build, "variant": variant, "scenario_id": group["id"],
              "scenario_family": group.get("family", group["app"]),
              "data_split": group["split"], "calibration": "calibration" in batch.name}
    start = time.monotonic()
    context = None
    try:
        instance.start()
        if group["app"] != "typesetter":
            instance.seed_training_page(fixture)
        with browser.new_page(viewport=VIEWPORT, device_scale_factor=1,
                              locale="en-US", timezone_id="UTC") as page:
            page.set_default_timeout(10000)
            login(page, instance.url, fixture)
            observer = observer_type(page, fixture)
            context = VisibleContext(directory, result, observer, page, group["app"], fixture)
            context.capture("Authenticated initial view")
            flow = wolf_evidence_flow(fixture, build) if group["app"] == "wolf" else make_flow(fixture, build)
            if variant == "cancelled":
                flow = cancelled_flow(group["app"], flow, fixture)
            elif variant == "input_not_established":
                flow = input_not_established_flow(flow)
            context.submit_step = flow.submit_step
            replay_flow(context, flow)
            oracle_verdict, reason = wolf_oracle(observer) if group["app"] == "wolf" else judge(observer.evidence)
            write_json(directory / "private/oracle.json", observer.data())
            if variant == "input_not_established":
                verify_unestablished_input(observer.evidence, fixture)
            label, steps = (wolf_label_from_visible(context.visible, flow, fixture, oracle_verdict)
                            if group["app"] == "wolf" else
                            label_from_visible(context.visible, flow, group, fixture, variant, oracle_verdict))
            parse_review(json.dumps(label), set(steps), profile)
            violation = review_guard(label, profile)
            if violation:
                raise ValueError(violation)
            write_json(directory / "label.json", label)
            write_json(directory / "public.json", {"task": task})
            frames = [{"step": step, "roles": ["public_observed_candidate"]} for step in steps]
            paths = {step: str((directory / f"screenshots/{step:03d}.png").relative_to(batch)) for step in steps}
            row = {"id": name, "phase": "review", "sample_kind": "review_curriculum",
                   "case_id": case, "workflow": "target", "build": build,
                   "data_split": group["split"], "scenario_id": group["id"],
                   "scenario_family": group.get("family", group["app"]),
                   "calibration": result["calibration"],
                   **identity, "review_format": "evidence_v2",
                   "review_profile": profile, "mechanism": group["app"], "variant": variant,
                   "label_source": "same_run_visible_fields_and_independent_oracle",
                   "run_directory": str(directory), "screenshot_steps": steps, "review_frames": frames,
                   "derived_provenance": {"source_run_id": name, "source_trace_sha256": file_hash(directory / "trace.jsonl"),
                                          "screenshot_steps": steps, "human_pixel_review": "pending",
                                          "deployment_manifest": str(directory / "private/instance/instance.json"),
                                          "deployment_manifest_sha256": file_hash(directory / "private/instance/instance.json")},
                   "messages": [{"role": "system", "content": [{"type": "text", "text": review_prompt(profile)}]},
                                {"role": "user", "content": review_content(task, context.history, frames, paths)},
                                {"role": "assistant", "content": json.dumps(label, ensure_ascii=False)}]}
            result.update(**identity, verdict=label["verdict"], oracle_verdict=oracle_verdict, oracle_reason=reason,
                          screenshot_steps=steps, source_hashes={
                              "trace.jsonl": file_hash(directory / "trace.jsonl"),
                              "private/oracle.json": file_hash(directory / "private/oracle.json"),
                              "private/visible.json": file_hash(directory / "private/visible.json"),
                              "private/instance/instance.json": file_hash(directory / "private/instance/instance.json"),
                              "label.json": file_hash(directory / "label.json"),
                              "public.json": file_hash(directory / "public.json"),
                              **{f"screenshots/{step:03d}.png": file_hash(directory / f"screenshots/{step:03d}.png")
                                 for step in steps}})
    except Exception:
        write_json(directory / "result.json", {**result, "complete": False})
        (directory / "private/error.txt").write_text(traceback.format_exc())
        if context is not None:
            write_json(directory / "private/oracle.json", context.observer.data())
        raise
    finally:
        instance.close()
    result.update(complete=True, elapsed_seconds=time.monotonic() - start)
    write_json(directory / "result.json", result)
    return row, result


def task_identity(group, fixture, task):
    app = group["app"]
    if app == "phpipam":
        initial = {key: fixture[key] for key in ("object_id", "address", "description", "hostname")}
        requested = fixture["changes"]
    elif app == "opencart":
        initial = {"product_id": 42, "option_label": fixture["option_label"],
                   "weight": fixture.get("initial_weight", "20"), "quantity": fixture.get("initial_quantity", 100)}
        requested = fixture["values"]
    elif app == "typesetter":
        initial = {"header": "Shoremark"}
        requested = {"first": fixture["first"], "final": fixture["input"]}
    else:
        initial = {"title": fixture["initial_title"], "slug": "about-us"}
        requested = {"kind": fixture["kind"], "fields": fixture["fields"]}
    return scenario_metadata(APPS[app][0], "target", task, initial, requested,
                             group.get("parent_scenario_id", app + ":" + group["id"]))


class VisibleContext(RunContext):
    def __init__(self, directory, result, observer, page, app, fixture):
        super().__init__(directory, result, observer, page)
        self.app, self.fixture, self.visible = app, fixture, {}
        self.submit_step = None

    def capture(self, description, action=None, raw=None, elapsed=0, error=None):
        if self.app == "opencart" and self.result["actions"] == self.submit_step:
            self.page.locator(".alert-success").first.wait_for(state="visible")
        if self.app == "phpipam":
            self.page.wait_for_function("""() => !jQuery('div.loading').is(':visible')
                && jQuery('#popupOverlay, #popupOverlay2, .popup').filter(':animated').length === 0""")
        path = super().capture(description, action, raw, elapsed, error)
        values = {}
        if self.app == "phpipam":
            for field in ("description", "hostname"):
                values[field.title()] = visible_reading(self.page.locator(f"input[name={field}]"))
        elif self.app == "opencart":
            modal = self.page.locator("#input-modal-weight")
            values["Weight input"] = visible_reading(modal)
            row = option_row(self.page, self.fixture)
            values["Weight"] = visible_reading(row.locator("td").nth(5)) if row.count() else None
        elif self.app == "typesetter":
            values["Header input"] = visible_reading(self.page.locator("input[name=value]"))
            values["Header"] = visible_reading(self.page.locator(self.fixture["area"]))
        else:
            values["Title"] = visible_reading(self.page.locator("#page_title"))
            values["Slug"] = visible_reading(self.page.locator("#page_slug"))
        messages = []
        for locator in self.page.locator(".alert, .messages li, #error, #success").all():
            value = visible_reading(locator)
            if value is not None:
                messages.append(value)
        snapshot = {"fields": values, "messages": messages, "url": self.page.url}
        if self.app == "phpipam":
            row = self.page.locator("tr").filter(has=self.page.locator(
                f".edit_ipaddress[data-id='{self.fixture['object_id']}']")).first
            snapshot["address_row"] = visible_reading(row)
            snapshot["render_state"] = self.page.evaluate("""() => ({
                loading_visible: jQuery('div.loading').is(':visible'),
                animated_popups: jQuery('#popupOverlay, #popupOverlay2, .popup').filter(':animated').length
            })""")
        self.visible[self.result["actions"]] = snapshot
        write_json(self.directory / "private/visible.json", self.visible)
        return path


def label_from_visible(visible, flow, group, fixture, variant, oracle_verdict):
    app = group["app"]
    input_step, final_step = flow.input_step, max(visible)
    before, after = visible[input_step]["fields"], visible[final_step]["fields"]
    fields = []
    if app == "phpipam":
        baseline_step = next(step for step, snapshot in visible.items()
                             if all(snapshot["fields"].get(key) is not None for key in ("Description", "Hostname")))
        for key in ("description", "hostname"):
            changed = key in fixture["changes"]
            requested = fixture["changes"].get(key, fixture[key])
            fields.append(field_label(key.title(), "edit" if changed else "preserve", requested,
                                      before[key.title()] if changed else visible[baseline_step]["fields"][key.title()],
                                      input_step if changed else baseline_step, after[key.title()], final_step))
    elif app == "opencart":
        baseline_step = next(step for step, snapshot in visible.items() if snapshot["fields"].get("Weight") is not None)
        fields.append(field_label("Weight", "edit", fixture["values"]["weight"],
                                  before["Weight input"], input_step, after["Weight"], final_step, numeric=True))
    else:
        baseline_step = 0
        fields.append(field_label("Header", "edit", fixture["input"], before["Header input"],
                                  input_step, after["Header"], final_step))
    if variant == "cancelled":
        if oracle_verdict != "inconclusive":
            raise ValueError("cancelled edit unexpectedly established a completed submission")
        status, submission, verdict = "unknown", None, "blocked"
        steps = sorted({baseline_step, input_step, input_step + 1, final_step})
        observation = "The requested input is visible, but the executed actions cancel the edit without submitting it. The reopened value does not establish an accepted save."
    else:
        messages = [message["value"] for message in visible[flow.submit_step]["messages"]]
        if any(any(word in text.lower() for word in ("success", "saved", "updated")) for text in messages):
            submission_text = "Visible submission response: " + " ".join(messages)[:250]
        elif (app == "phpipam" and visible[flow.submit_step]["fields"]["Description"] is None
              and visible[flow.submit_step].get("address_row") is not None
              and fixture["address"] in visible[flow.submit_step]["address_row"]["value"]):
            submission_text = f"After clicking Save, the address-edit dialog closes and the list visibly identifies the same address {fixture['address']}."
        else:
            raise ValueError(f"accepted submission has no visible response at step {flow.submit_step}: {messages}")
        status = "accepted"
        submission = {"step": flow.submit_step, "observation": submission_text}
        steps = sorted({baseline_step, input_step, flow.submit_step, final_step})
        if variant == "input_not_established":
            if oracle_verdict != "inconclusive" or any(
                    field["input_matches_request"] is not False for field in fields if field["intent"] == "edit"):
                raise ValueError("input-not-established evidence must show unmatched input and an inconclusive oracle")
            verdict = "blocked"
            observation = ("Before Save, the requested empty field still visibly contains its original text. "
                           + submission_text + " After reopening the same address, the original text remains. "
                           "The requested empty input was not established, so this test is blocked.")
        else:
            if any(field["input_matches_request"] is not True for field in fields if field["intent"] == "edit"):
                raise ValueError("visible input does not match the requested value in a saved test")
            if oracle_verdict not in {"pass", "fail"}:
                raise ValueError(f"independent oracle did not complete the requested operation: {oracle_verdict}")
            verdict = "pass" if all(field["result_matches_request"] for field in fields) else "fail"
            if verdict != oracle_verdict:
                raise ValueError("visible readings and the independent oracle disagree")
            observation = "The requested input is visible before Save. " + submission_text + " After reopening the same record, the displayed values " + ("match the task." if verdict == "pass" else "differ from the task.")
    return {"action": "finish", "task_kind": "edit", "field_readings": fields,
            "submission": {"status": status, "evidence": submission}, "result_observed": True,
            "result_steps": [final_step], "expected_rejection": False, "verdict": verdict,
            "observation": observation, "evidence_steps": steps}, steps


def field_label(name, intent, requested, before, before_step, after, after_step, numeric=False):
    if before is None or after is None:
        raise ValueError(f"{name}: necessary field is outside the captured viewport")
    matches = (Decimal(after["value"].lstrip("+")) == Decimal(requested)) if numeric else after["value"] == requested
    input_matches = (Decimal(before["value"]) == Decimal(requested)) if numeric else before["value"] == requested
    return {"field": name, "intent": intent, "requested_value": requested,
            "before": {"value": before["value"], "step": before_step},
            "after": {"value": after["value"], "step": after_step},
            "input_matches_request": input_matches if intent == "edit" else None,
            "result_matches_request": matches}


def wolf_evidence_flow(fixture, build):
    flow = wolf_flow(fixture, build)
    if fixture["kind"] == "duplicate":
        return flow
    # Title and Slug occupy separate tabs, so both require their own screenshot.
    steps = [*flow.steps[:flow.input_step], click_at('a[href="#metadata"]'),
             *flow.steps[flow.input_step:], click_at('a[href="#metadata"]')]
    return SftFlow(steps, flow.input_step, flow.submit_step + 1,
                   navigation_steps=tuple(step + 1 if step > flow.input_step else step
                                          for step in flow.navigation_steps))


def wolf_label_from_visible(visible, flow, fixture, oracle_verdict):
    duplicate = fixture["kind"] == "duplicate"
    submission_step = flow.submit_step
    messages = [message["value"] for message in visible[submission_step]["messages"]]
    rejection = next((text for text in messages if "Page with slug" in text and "already exists!" in text), None)
    success = next((text for text in messages if "Page has been saved!" in text), None)
    status = "rejected" if rejection else "accepted" if success else "unknown"
    fields, steps, result_steps = [], [], []
    for name, requested in (("Title", fixture["fields"]["title"]), ("Slug", "about-us")):
        before_step = max(step for step, snapshot in visible.items() if step < submission_step
                          and snapshot["fields"].get(name) is not None)
        before = visible[before_step]["fields"][name]["value"]
        intent = "edit" if name == "Title" or duplicate else "preserve"
        if before != requested:
            raise ValueError(f"Wolf {name}: visible submitted input differs from the task")
        after = None
        if status == "accepted" and not duplicate:
            after_step = max(step for step, snapshot in visible.items() if step > submission_step
                             and snapshot["fields"].get(name) is not None)
            after = {"value": visible[after_step]["fields"][name]["value"], "step": after_step}
            result_steps.append(after_step)
        fields.append({"field": name, "intent": intent, "requested_value": requested,
                       "before": {"value": before, "step": before_step}, "after": after,
                       "input_matches_request": True if intent == "edit" else None,
                       "result_matches_request": None if after is None else after["value"] == requested})
        steps.append(before_step)
    if status == "rejected":
        verdict = "pass" if duplicate else "fail"
        explanation = ("The public task asks to verify rejection of a new page with an existing sibling slug."
                       if duplicate else "The public task edits the existing page while preserving its own slug; this save should succeed.")
    elif status == "accepted" and not duplicate:
        verdict = "pass" if all(field["result_matches_request"] for field in fields) else "fail"
        explanation = "The saved existing page is reopened; its Title and Slug are compared with the public task."
    else:
        verdict, explanation = "blocked", "The screenshots do not establish the requested business outcome."
    if verdict != "blocked" and verdict != oracle_verdict:
        raise ValueError("Wolf visible business outcome and independent oracle disagree")
    steps = sorted(set([*steps, submission_step, *result_steps]))
    event = rejection or success
    return {"action": "finish", "task_kind": "create" if duplicate else "edit", "field_readings": fields,
            "submission": {"status": status, "evidence": {"step": submission_step, "observation": event}
                           if event else None}, "result_observed": bool(result_steps),
            "result_steps": sorted(set(result_steps)), "expected_rejection": duplicate,
            "verdict": verdict, "observation": explanation + (" Visible response: " + event if event else ""),
            "evidence_steps": steps}, steps


def visible_reading(locator):
    if not locator.count() or not locator.first.is_visible() or not in_view(locator.first):
        return None
    item = locator.first
    tag = item.evaluate("element => element.tagName.toLowerCase()")
    value = item.input_value() if tag in {"input", "textarea", "select"} else item.inner_text().strip()
    return {"value": value, "bounding_box": item.bounding_box(), "in_view": True,
            "dom_text": item.inner_text(), "tag": tag}


def task_fixture(group):
    case = APPS[group["app"]][0]
    fixture = json.loads((ROOT / "cases" / case / "fixtures.private.json").read_text())["target"]
    fixture.update(copy.deepcopy(group["fixture"]))
    if group["app"] == "phpipam":
        expected = {key: fixture["changes"].get(key, fixture[key]) for key in ("description", "hostname")}
        fixture["expected"] = json.dumps(expected, sort_keys=True)
        changed = ", ".join(f"{key.title()} to {value!r}" for key, value in fixture["changes"].items())
        retained = ", ".join(f"keep {key.title()} as {value!r}" for key, value in expected.items() if key not in fixture["changes"])
        task = f"In phpIPAM, edit address {fixture['address']}: set {changed}; {retained}. Save, reopen the same address and inspect both fields. Report pass if the accepted save preserves the requested values, fail if the reopened values differ, or blocked if no accepted save is established."
    elif group["app"] == "opencart":
        fixture["expected"] = json.dumps(fixture["values"], sort_keys=True)
        fixture.update(start="product", field_order=["weight"])
        task = f"In Apple Cinema 30 product Options, edit the {fixture['option_label']} Select value Weight to {fixture['values']['weight']}. Save the modal and product, then reload and reopen Options. Inspect the same {fixture['option_label']} row. Report pass if its displayed weight matches, fail if an accepted save leaves a different weight, or blocked if no accepted product save is established."
    elif group["app"] == "typesetter":
        fixture["expected"] = fixture["input"]
        task = f"In Typesetter, change the homepage Header from Shoremark to {fixture['first']!r} and save. Reopen the homepage, then change that same Header to {fixture['input']!r} and save again. Reopen the homepage and inspect the Header. Report pass if the final accepted save displays {fixture['input']!r}, fail if a different Header remains, or blocked if the requested saves cannot be established."
    else:
        fixture["start"] = "editor"
        if fixture["kind"] == "duplicate":
            fixture["expected"] = "duplicate_slug_rejected"
            task = f"In Wolf CMS, add a new child page under Home Page. Set Title to {fixture['fields']['title']!r}. In Metadata set Slug to about-us, already used by an existing sibling. Use Save and Continue Editing to verify that duplicate slugs are rejected. Report pass if the duplicate is visibly rejected, fail if it is saved, or blocked if the outcome cannot be established."
        else:
            fixture["expected"] = json.dumps(fixture["fields"], sort_keys=True)
            task = f"In Wolf CMS, edit the existing page titled {fixture['initial_title']!r}. Set Title to {fixture['fields']['title']!r}, preserve its own Slug about-us, and use Save and Continue Editing. The existing page should save with its own slug. Reload and inspect Title and Slug. Report pass when the edit persists, fail if the save rejects this valid existing-page edit or the reopened values differ, or blocked if the outcome cannot be established."
    return fixture, task


def cancelled_flow(app, flow, fixture):
    steps = flow.steps[:flow.input_step]
    if app == "phpipam":
        steps += [press("Escape"), press("Control+r"), *flow.steps[:2]]
    else:
        steps += [click_at("#modal-option .btn-close"), press("Control+r"),
                  scroll_to('a[href="#tab-option"]'), click_at('a[href="#tab-option"]'),
                  scroll_to(f'tr[id^="option-value-row-"]:has-text("{fixture["option_label"]}")')]
    return SftFlow(steps, flow.input_step, 0)


def input_not_established_flow(flow):
    steps = list(flow.steps)
    steps[flow.input_step - 1] = press("ArrowRight")
    return flow._replace(steps=steps)


def verify_unestablished_input(evidence, fixture):
    original = json.dumps({key: fixture[key] for key in ("description", "hostname")}, sort_keys=True)
    if not (evidence.healthy and evidence.preconditions and evidence.submitted is False
            and evidence.response_valid and evidence.reopened and evidence.same_object
            and evidence.actual == original):
        raise ValueError("input-not-established collection requires an accepted original-value submission and same-record reopen")


def load_catalog(path):
    catalog = yaml.safe_load(path.read_text())
    groups = catalog["groups"]
    if len({(group["app"], group["id"]) for group in groups}) != len(groups):
        raise ValueError("curriculum group identifiers must be unique within each application")
    for group in groups:
        if group["app"] not in APPS or group["split"] not in {"train", "validation"}:
            raise ValueError("curriculum must use supported training applications and declared partitions")
    return catalog


def collection_jobs(catalog, args):
    groups = [group for group in catalog["groups"] if not args.app or group["app"] in args.app]
    if args.input_not_established_only:
        groups = [group for group in groups if group["app"] == "phpipam"]
    if args.calibration:
        selected, seen = [], set()
        for group in groups:
            key = (group["app"], group["fixture"].get("kind") if group["app"] == "wolf" else None)
            if key not in seen:
                selected.append(group)
                seen.add(key)
        groups = selected
    variant = "input_not_established" if args.input_not_established_only else "saved"
    jobs = [(group, build, variant) for group in groups for build in
            (("A",) if args.calibration and group["app"] not in {"wolf", "phpipam"}
             else group.get("builds", ("A", "B")))]
    if args.include_blocked and not args.calibration:
        jobs += [(group, "B", "cancelled") for group in groups if group["app"] in {"phpipam", "opencart"}]
        if catalog.get("group_ordered_variants"):
            jobs = [job for group in groups for job in jobs if job[0] is group]
    return jobs


def shard_jobs(jobs, shard_index, shards):
    parents = list(dict.fromkeys(group.get("parent_scenario_id", group["app"] + ":" + group["id"])
                                 for group, _, _ in jobs))
    selected = set(parents[shard_index::shards])
    return [job for job in jobs if job[0].get("parent_scenario_id", job[0]["app"] + ":" + job[0]["id"]) in selected]


def snapshot_source(batch, suffix=""):
    paths = ["src/guiqa/rl_targeted_collection.py", "scripts/collect_rl_targeted.py", "src/guiqa/runner.py",
             "src/guiqa/sft_data.py", "src/guiqa/sft_flows.py", "src/guiqa/native.py", "src/guiqa/browser.py",
             "src/guiqa/apps/impress_opencart.py", "src/guiqa/apps/mantis_phpipam.py",
             "src/guiqa/apps/litecart_wolf.py",
             "src/guiqa/training_cases.py", "src/guiqa/training_instances.py", "src/guiqa/oracle.py",
             "src/guiqa/review_context.py", "src/guiqa/review_contract.py"]
    paths.append("src/guiqa/review_scenarios.py")
    archive_name = "collector-source" + suffix + ".tar.gz"
    manifest_name = "collector-source" + suffix + ".json"
    with tarfile.open(batch / archive_name, "w:gz") as archive:
        for path in paths:
            archive.add(ROOT / path, arcname=path)
    write_json(batch / manifest_name, {path: file_hash(ROOT / path) for path in paths})
    return {"archive": archive_name, "source_manifest": manifest_name,
            "source_manifest_sha256": file_hash(batch / manifest_name)}


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "configs/rl-targeted-curriculum.yaml")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--app", choices=APPS, action="append")
    parser.add_argument("--port", type=int, default=13260)
    variants = parser.add_mutually_exclusive_group()
    variants.add_argument("--include-blocked", action="store_true")
    variants.add_argument("--input-not-established-only", action="store_true")
    parser.add_argument("--calibration", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    args = parser.parse_args()
    if not 0 <= args.shard_index < args.shards:
        parser.error("shard-index must be between zero and shards-1")
    if args.input_not_established_only and args.app and args.app != ["phpipam"]:
        parser.error("--input-not-established-only uses the phpIPAM training application")
    return args
