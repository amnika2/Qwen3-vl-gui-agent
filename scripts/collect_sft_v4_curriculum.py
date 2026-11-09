"""Collect real automatic-copy and cancelled-edit screenshot review examples."""

import argparse
import hashlib
import json
import shutil
import tarfile
import time
import traceback
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml
from playwright.sync_api import sync_playwright

from guiqa.apps.impress_opencart import OpencartInstance, opencart_login
from guiqa.artifacts import write_json
from guiqa.browser import VIEWPORT, execute, settle
from guiqa.review_context import review_content
from guiqa.review_contract import parse_review, review_guard, review_prompt
from guiqa.review_scenarios import scenario_metadata
from guiqa.oracle import in_view
from guiqa.sft_flows import click_at, click_locator, press, type_text

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "configs/sft-v4-curriculum.yaml")
    parser.add_argument("--port", type=int, default=13235)
    parser.add_argument("--only", action="append")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    catalog = yaml.safe_load(args.catalog.read_text())
    groups = load_groups(catalog)
    batch = (args.output or ROOT / "runs" / ("sft-v4-curriculum-" + uuid.uuid4().hex[:8])).resolve()
    jobs = [(group, variant) for group in groups for variant in ("automatic_copy", "cancelled_edit")
            if not args.only or "opencart-" + group["id"] + "-" + variant in args.only]
    if args.resume:
        manifest = json.loads((batch / "manifest.json").read_text())
        if manifest["catalog_sha256"] != hashlib.sha256(args.catalog.read_bytes()).hexdigest():
            raise ValueError("resume requires the frozen copy/cancel catalog")
    else:
        batch.mkdir(parents=True, exist_ok=False)
        shutil.copy2(args.catalog, batch / "catalog.yaml")
        files = [Path(__file__), ROOT / "src/guiqa/review_scenarios.py", ROOT / "src/guiqa/review_contract.py",
                 ROOT / "src/guiqa/review_context.py", ROOT / "src/guiqa/browser.py",
                 ROOT / "src/guiqa/apps/impress_opencart.py", ROOT / "src/guiqa/sft_flows.py"]
        with tarfile.open(batch / "collector-source.tar.gz", "w:gz") as archive:
            for path in files:
                archive.add(path, arcname=str(path.relative_to(ROOT)))
        write_json(batch / "collector-source.json", {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                                     for path in files})
        manifest = {"case_id": "sweeper-125", "review_format": "evidence_v2", "review_only": True,
                    "teacher_model_calls": 0, "calibration": False, "runs_planned": len(jobs),
                    "samples_planned": len(jobs), "completed": [], "failures": [],
                    "collector_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    "catalog_sha256": hashlib.sha256(args.catalog.read_bytes()).hexdigest()}
    write_json(batch / "manifest.json", manifest)
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            for group, variant in jobs:
                    job_id = "opencart-" + group["id"] + "-" + variant
                    if any(record.get("job_id", record["run_id"]) == job_id for record in manifest["completed"]):
                        continue
                    attempt, name = 1, job_id
                    while (batch / name).exists():
                        attempt += 1
                        name = f"{job_id}-attempt-{attempt}"
                    directory = batch / name
                    try:
                        row, result = collect(browser, directory, group, variant, catalog["build"], args.port)
                    except Exception as error:
                        manifest["failures"].append({"job_id": job_id, "run_id": name,
                                                     "error": f"{type(error).__name__}: {error}"})
                        write_json(batch / "manifest.json", manifest)
                        print(json.dumps(manifest["failures"][-1]), flush=True)
                        continue
                    result["job_id"] = job_id
                    write_json(directory / "result.json", result)
                    for filename in ("reviews.jsonl", f"{group['split']}.jsonl"):
                        with (batch / filename).open("a") as output:
                            output.write(json.dumps(row, ensure_ascii=False) + "\n")
                    manifest["completed"].append(result)
                    write_json(batch / "manifest.json", manifest)
                    print(json.dumps(result, ensure_ascii=False), flush=True)
    manifest.update(complete=len(manifest["completed"]) == len(jobs),
                    completed_runs=len(manifest["completed"]), review_samples=len(manifest["completed"]),
                    samples=len(manifest["completed"]))
    manifest["dataset_sha256"] = {name: hashlib.sha256((batch / name).read_bytes()).hexdigest()
                                 for name in ("reviews.jsonl", "train.jsonl", "validation.jsonl") if (batch / name).exists()}
    write_json(batch / "manifest.json", manifest)
    print(json.dumps({"batch": str(batch), "curriculum": str(batch / "reviews.jsonl")}), flush=True)
    if not manifest["complete"]:
        raise SystemExit(4)


def load_groups(catalog):
    if catalog["case_id"] != "sweeper-125" or catalog["app"] != "opencart" or catalog["build"] not in {"A", "B"}:
        raise ValueError("curriculum must use the OpenCart training application")
    groups = catalog["groups"]
    for group in groups:
        if group["split"] not in {"train", "validation"}:
            raise ValueError("curriculum split must be train or validation")
        if any(not isinstance(group[key], str) or not group[key].strip()
               for key in ("id", "name", "model", "edited_name")):
            raise ValueError("curriculum identifiers and product values must be nonempty strings")
    if len({group["id"] for group in groups}) != len(groups):
        raise ValueError("curriculum group IDs must be unique")
    return groups


def collect(browser, directory, group, variant, build, port):
    started = time.monotonic()
    directory.mkdir()
    private = directory / "private"
    private.mkdir()
    instance = OpencartInstance(ROOT, private / "instance", build, port)
    try:
        instance.start()
        name, model = group["name"].replace("'", "''"), group["model"].replace("'", "''")
        instance.sql("USE guiqa; DELETE FROM oc_product WHERE product_id<>42; "
                     f"UPDATE oc_product SET status=1,model='{model}' WHERE product_id=42; "
                     f"UPDATE oc_product_description SET name='{name}' WHERE product_id=42;")
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1, locale="en-US", timezone_id="UTC")
        opencart_login(page, instance.url, {"username": "lina.croft", "password": "amberQuilt7!", "start": "product"})
        if variant == "automatic_copy":
            token = parse_qs(urlparse(page.url).query)["user_token"][0]
            page.goto(instance.url + "/admin/index.php?route=catalog/product&user_token=" + token,
                      wait_until="domcontentloaded")
            settle(page)
        recording = Recording(page, directory)
        page.on("response", recording.response)
        recording.capture(None)
        before = instance.sql("USE guiqa; SELECT p.product_id,p.status,p.model,pd.name "
                              "FROM oc_product p JOIN oc_product_description pd USING(product_id) "
                              "WHERE pd.language_id=1 ORDER BY p.product_id;")
        if variant == "automatic_copy":
            steps, label = automatic_copy(recording, group)
            task = (
                f"In OpenCart Products, the list currently contains one product named {group['name']!r} "
                f"with Model {group['model']!r}. Select that row and use Copy exactly once to create "
                "a duplicate, without typing a name or editing the original product. Verify the "
                "list grows from one to two rows, the original name remains unchanged, and the "
                "new disabled (dimmed) row retains the source Product Name. Open that new row "
                "with Edit, reload, and inspect Product Name. Report pass if the duplicate is "
                "created with the correct name and the original remains, fail for a visibly "
                "incorrect completed copy, or blocked if creation cannot be established."
            )
        else:
            steps, label = cancelled_edit(recording, group)
            task = (
                f"In OpenCart, change this product's Product Name from {group['name']!r} to "
                f"{group['edited_name']!r}, use Save, then reopen and reload the product. "
                "Report pass if the saved Product Name matches, fail if an accepted save "
                "leaves a different name, or blocked if the requested edit was not submitted."
            )
        after = instance.sql("USE guiqa; SELECT p.product_id,p.status,p.model,pd.name "
                             "FROM oc_product p JOIN oc_product_description pd USING(product_id) "
                             "WHERE pd.language_id=1 ORDER BY p.product_id;")
        write_json(private / "oracle.json", {
            "before_products_tsv": before, "after_products_tsv": after,
            "network": recording.network, "ui": recording.ui,
            "source_product_id": 42, "expected_variant": variant,
        })
        label = observed_copy_label(recording, group, variant, label, before, after)
        parse_review(json.dumps(label), set(steps))
        violation = review_guard(label)
        if violation:
            raise ValueError(violation)
        frames = [{"step": step, "roles": ["public_curriculum_candidate"]} for step in steps]
        paths = {step: str(directory / f"screenshots/{step:03d}.png") for step in steps}
        trace_sha = hashlib.sha256((directory / "trace.jsonl").read_bytes()).hexdigest()
        provenance = {
            "curriculum": True, "source_run_id": directory.name, "source_trace_sha256": trace_sha,
            "history_end_step": recording.step, "screenshot_steps": steps,
            "deployment_manifest": str(private / "instance/instance.json"),
            "deployment_manifest_sha256": hashlib.sha256((private / "instance/instance.json").read_bytes()).hexdigest(),
        }
        identity = scenario_metadata("sweeper-125", "preservation", task,
                                     {"product_id": 42, "name": group["name"], "model": group["model"]},
                                     ({"new_product_name": group["name"], "preserved_original_name": group["name"]}
                                      if variant == "automatic_copy" else {"product_name": group["edited_name"]}),
                                     group.get("parent_scenario_id", "opencart:" + group["id"]))
        row = {
            "id": directory.name,
            "phase": "review", "sample_kind": "review_curriculum", "case_id": "sweeper-125",
            "workflow": "preservation", "build": build, "data_split": group["split"],
            "scenario_id": group["id"], **identity, "calibration": False,
            "source_run_id": directory.name, "run_directory": str(directory), "variant": variant,
            "review_format": "evidence_v2", "review_profile": "persistence",
            "label_source": "observed_run_v4_curriculum", "step": recording.step,
            "screenshot": paths[steps[-1]], "screenshot_steps": steps, "review_frames": frames,
            "derived_provenance": provenance,
            "messages": [
                {"role": "system", "content": [{"type": "text", "text": review_prompt()}]},
                {"role": "user", "content": review_content(task, recording.history, frames, paths)},
                {"role": "assistant", "content": json.dumps(label, ensure_ascii=False)},
            ],
            "assistant": label,
        }
        result = {
            "run_id": directory.name, "scenario_id": group["id"], "case_id": "sweeper-125", "variant": variant,
            "workflow": "preservation", "build": build, "calibration": False, **identity,
            "data_split": group["split"], "directory": str(directory),
            "actions": recording.step, "verdict": label["verdict"], "screenshot_steps": steps,
            "source_trace_sha256": trace_sha,
        }
        write_json(directory / "public.json", {"task": task})
        write_json(directory / "label.json", label)
        result.update(complete=True, elapsed_seconds=time.monotonic() - started, oracle_verdict=label["verdict"],
                      source_hashes={name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in
                                     ["trace.jsonl", "private/oracle.json", "private/visible.json",
                                      "private/instance/instance.json", "label.json", "public.json",
                                      *[f"screenshots/{step:03d}.png" for step in steps]]})
        write_json(directory / "result.json", result)
        page.close()
        return row, result
    except Exception:
        write_json(directory / "result.json", {"run_id": directory.name, "complete": False})
        (private / "error.txt").write_text(traceback.format_exc())
        raise
    finally:
        instance.close()


def observed_copy_label(recording, group, variant, label, before, after):
    ui = {entry["step"]: entry for entry in recording.ui}
    rows_before = {row.split("\t", 1)[0]: row.split("\t") for row in before.splitlines()}
    rows_after = {row.split("\t", 1)[0]: row.split("\t") for row in after.splitlines()}
    if variant == "automatic_copy":
        created = set(rows_after) - set(rows_before)
        if len(rows_before) != 1 or len(created) != 1 or len(rows_after) != 2:
            raise ValueError("Copy must establish exactly one new product beside the original")
        new_id = next(iter(created))
        if ui[5]["product_id"] != new_id or ui[5]["product_name"] is None:
            raise ValueError("Copy evidence does not reopen the newly created product")
        if group["name"] not in " ".join(ui[0]["rows"]) or group["name"] not in " ".join(ui[3]["rows"]):
            raise ValueError("Copy evidence does not show the original named product")
        fields = label["field_readings"]
        fields[0]["after"]["value"] = ui[5]["product_name"]
        fields[0]["result_matches_request"] = ui[5]["product_name"] == group["name"]
        fields[1]["after"]["value"] = rows_after["42"][3]
        fields[1]["result_matches_request"] = rows_after["42"][3] == group["name"]
        fields[2]["before"]["value"] = str(len(ui[0]["rows"]))
        fields[2]["after"]["value"] = str(len(ui[3]["rows"]))
        fields[2]["result_matches_request"] = len(ui[3]["rows"]) == 2
        label["verdict"] = "pass" if all(field["result_matches_request"] for field in fields) else "fail"
    else:
        if any(event["method"] == "POST" and "product.save" in event["route"] for event in recording.network):
            raise ValueError("cancelled edit unexpectedly submitted the product")
        if ui[3]["product_name"] is None or ui[6]["product_name"] is None:
            raise ValueError("cancelled edit is missing its before/reopened visible name")
        field = label["field_readings"][0]
        field["before"]["value"], field["after"]["value"] = ui[3]["product_name"], ui[6]["product_name"]
        field["input_matches_request"] = ui[3]["product_name"] == group["edited_name"]
        field["result_matches_request"] = ui[6]["product_name"] == group["edited_name"]
    return label


def automatic_copy(recording, group):
    recording.act(click_at('input[name="selected[]"][value="42"]'))
    recording.act(click_at('button[formaction*="product.copy"]'))
    recording.page.locator('input[name="selected[]"]').nth(1).wait_for()
    recording.act(press("Control+r"))
    recording.act(click_locator(lambda page: page.locator('#form-product tbody tr').nth(1).locator('a[title="Edit"]')))
    recording.act(press("Control+r"))
    label = {
        "action": "finish", "task_kind": "create", "field_readings": [
            {"field": "Product Name", "intent": "generated", "requested_value": group["name"],
             "before": None, "after": {"value": group["name"], "step": 5},
             "input_matches_request": None, "result_matches_request": True},
            {"field": "Original Product Name", "intent": "preserve", "requested_value": group["name"],
             "before": {"value": group["name"], "step": 0},
             "after": {"value": group["name"], "step": 3},
             "input_matches_request": None, "result_matches_request": True},
            {"field": "Product list count", "intent": "generated", "requested_value": "2",
             "before": {"value": "1", "step": 0}, "after": {"value": "2", "step": 3},
             "input_matches_request": None, "result_matches_request": True},
        ],
        "submission": {"status": "accepted", "evidence": {
            "step": 2, "observation": "After Copy, a success message and the second product row appear."}},
        "result_observed": True, "result_steps": [3, 4, 5], "expected_rejection": False,
        "verdict": "pass", "observation": (
            f"The list initially shows one {group['name']!r} row. Copy produces a success response "
            "and a new dimmed row; after list reload both rows remain. Opening the new second "
            f"row and reloading shows Product Name {group['name']!r}. The original name is "
            "unchanged. No typing is required for this copy-created name."
        ), "evidence_steps": [0, 2, 3, 4, 5],
    }
    return [0, 2, 3, 4, 5], label


def cancelled_edit(recording, group):
    recording.act(click_at("#input-name-1"))
    recording.act(press("Control+a"))
    recording.act(type_text(group["edited_name"]))
    recording.act(click_at('a[title="Back"]'))
    recording.act(click_locator(lambda page: page.locator('#form-product tbody tr').first.locator('a[title="Edit"]')))
    recording.act(press("Control+r"))
    label = {
        "action": "finish", "task_kind": "edit", "field_readings": [{
            "field": "Product Name", "intent": "edit", "requested_value": group["edited_name"],
            "before": {"value": group["edited_name"], "step": 3},
            "after": {"value": group["name"], "step": 6},
            "input_matches_request": True, "result_matches_request": False,
        }],
        "submission": {"status": "unknown", "evidence": {
            "step": 4, "observation": "Back was clicked instead of Save; the product list returns without a save response."}},
        "result_observed": True, "result_steps": [6], "expected_rejection": False,
        "verdict": "blocked", "observation": (
            f"Product Name visibly contains {group['edited_name']!r} at step 3, but the next "
            "action uses Back rather than Save. After reopening and reload, the original "
            f"name {group['name']!r} remains. This is an unsubmitted edit, so the old name "
            "does not establish a persistence defect."
        ), "evidence_steps": [0, 3, 4, 6],
    }
    return [0, 3, 4, 6], label


class Recording:
    def __init__(self, page, directory):
        self.page, self.directory = page, directory
        self.step = 0
        self.history, self.network, self.ui = [], [], []
        (directory / "screenshots").mkdir()

    def act(self, make_action):
        action = make_action(self.page)
        self.step += 1
        execute(self.page, action)
        self.capture(action)

    def capture(self, action):
        self.page.wait_for_function("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        path = f"screenshots/{self.step:03d}.png"
        self.page.screenshot(path=str(self.directory / path))
        trace = {"step": self.step, "action": action, "screenshot": path, "execution_error": None,
                 "screenshot_size": [VIEWPORT["width"], VIEWPORT["height"]]}
        with (self.directory / "trace.jsonl").open("a") as output:
            output.write(json.dumps(trace) + "\n")
        self.history.append({"step": self.step, "action": action, "result": "executed"})
        rows = self.page.locator('#form-product tbody tr')
        name = self.page.locator('#input-name-1')
        self.ui.append({"step": self.step, "route": parse_qs(urlparse(self.page.url).query).get("route"),
                        "rows": rows.all_text_contents(),
                        "product_id": parse_qs(urlparse(self.page.url).query).get("product_id", [None])[0],
                        "selected_ids": self.page.locator('input[name="selected[]"]').evaluate_all("elements => elements.map(e => e.value)"),
                        "product_name": name.input_value() if name.count() and name.is_visible() and in_view(name) else None})
        write_json(self.directory / "private/visible.json", self.ui)

    def response(self, response):
        query = parse_qs(urlparse(response.url).query)
        route = query.get("route", [""])[0]
        if not route.startswith("catalog/product"):
            return
        entry = {"step": self.step, "route": route, "status": response.status,
                 "method": response.request.method}
        if response.request.method == "POST":
            entry.update(post_data=response.request.post_data, body=response.text())
        self.network.append(entry)


if __name__ == "__main__":
    main()
