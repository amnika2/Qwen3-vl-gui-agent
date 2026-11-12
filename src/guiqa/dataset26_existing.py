"""Collect the seven reusable applications for the new application-held-out dataset."""

import argparse
import base64
import json
import shutil
import subprocess
import time
import traceback
from decimal import Decimal
from pathlib import Path

from playwright.sync_api import sync_playwright

from .artifacts import write_json
from .apps.impress_opencart import OpencartObserver
from .browser import VIEWPORT, settle
from .dataset26_runtime import DatasetRecorder, import_trace
from .final_test_catalog import make_group
from .final_test_collection import collect_one as collect_test
from .rl_targeted_collection import collect_one as collect_targeted
from .rl_targeted_collection import APPS as TARGETED_APPS
from .training_cases import wondercms_login
from .training_instances import DokuwikiInstance, FlatpressInstance, WondercmsInstance


ROOT = Path(__file__).resolve().parents[2]
CASES = {
    "sweeper-125": ("F", "opencart", 25),
    "sweeper-142": ("F", "phpipam", 25),
    "sweeper-195": ("V", "wolf", 25),
    "sweeper-196": ("L", "wondercms", 25),
    "sweeper-034": ("N", "dokuwiki", 25),
    "sweeper-053": ("N", "flatpress", 25),
    "sweeper-005": ("F", "akaunting", 10),
}
SUBJECTS = ["Harbor", "Cedar", "Museum", "Garden", "Studio", "Library", "Orchard", "River", "Market",
            "Lake", "Archive", "Theatre", "Coffee", "Science", "Trail", "Textile", "Pottery", "Book", "Autumn",
            "Spring", "Valley", "Forest", "School", "Station", "Meadow"]
PIXEL = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aT1kAAAAASUVORK5CYII=")


def main():
    args = arguments()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cases = args.case or list(CASES)
    catalog = [make_spec(case, index) for case in cases
               for index in range(1, CASES[case][2] + 1)]
    if args.calibration:
        catalog = [make_spec(case, 101) for case in cases]
    tag = "existing-" + "-".join(case.rsplit("-", 1)[1] for case in cases)
    write_json(output / (tag + ("-calibration-catalog.json" if args.calibration else "-catalog.json")), catalog)
    summary_path = output / (tag + ("-calibration-progress.json" if args.calibration else "-progress.json"))
    status = {"complete": False, "planned": len(catalog) * 2, "completed": [], "failures": [],
              "started_at": time.time(), "calibration": args.calibration}
    write_json(summary_path, status)
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            for spec in catalog:
                for build in ("A", "B"):
                    directory = run_directory(output, spec, build, args.calibration)
                    result_file = directory / "result.json"
                    if directory.exists():
                        previous = json.loads(result_file.read_text()) if result_file.exists() else {}
                        if previous.get("complete") and previous.get("operation_samples") == 5:
                            status["completed"].append(previous)
                            continue
                        if not args.retry_failed:
                            raise RuntimeError(f"incomplete run exists: {directory}; inspect it before --retry-failed")
                        archive = directory.with_name(directory.name + f"-failed-{time.time_ns()}")
                        directory.rename(archive)
                    try:
                        result = collect(browser, spec, build, directory, args.port)
                    except Exception:
                        directory.mkdir(parents=True, exist_ok=True)
                        (directory / "error.txt").write_text(traceback.format_exc())
                        failure = {"case_id": spec["case_id"], "instance_id": spec["instance_id"],
                                   "build": build, "directory": str(directory), "error": traceback.format_exc()}
                        write_json(result_file, {**metadata(spec, build), "complete": False, "error": failure["error"]})
                        status["failures"].append(failure)
                        write_json(summary_path, status)
                        print(json.dumps(failure), flush=True)
                        raise
                    status["completed"].append(result)
                    write_json(summary_path, status)
                    print(json.dumps({"case_id": spec["case_id"], "instance_id": spec["instance_id"],
                                      "build": build, "verdict": result["verdict"], "complete": True}), flush=True)
                    if args.limit and len(status["completed"]) >= args.limit:
                        return
    status["complete"] = len(status["completed"]) == status["planned"]
    status["finished_at"] = time.time()
    write_json(summary_path, status)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "runs/dataset26-v1/collections")
    parser.add_argument("--case", choices=CASES, action="append")
    parser.add_argument("--port", type=int, default=14500)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--calibration", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    return parser.parse_args()


def make_spec(case, index):
    category, app, _ = CASES[case]
    subject = SUBJECTS[(index - 1) % len(SUBJECTS)]
    spec = {"case_id": case, "category": category, "app": app,
            "split": "test" if case == "sweeper-005" else "train", "index": index,
            "instance_id": f"d26-{case}-{index:03d}", "subject": subject}
    if app == "opencart":
        decimal = index % 5 != 0
        spec.update(task_family_id="opencart.edit_option_weight", condition="decimal_weight" if decimal else "integer_weight",
                    fixture={"option_label": ["Red", "Blue", "Green", "Yellow"][(index - 1) % 4],
                             "initial_weight": str(30 + index), "initial_quantity": 100 + index,
                             "values": {"weight": f"{5 + index}.375" if decimal else str(5 + index)}})
    elif app == "phpipam":
        mode = index % 5
        changes = ({"description": ""} if mode in (0, 1) else {"hostname": ""} if mode == 2
                   else {"description": "", "hostname": ""} if mode == 3
                   else {"hostname": f"{subject.lower()}-new-{index}.lab.test"})
        spec.update(task_family_id="phpipam.edit_ip_fields", condition=["clear_description", "clear_description",
                     "clear_hostname", "clear_both", "replace_hostname"][mode],
                    fixture={"object_id": 1 if index % 2 else 2, "address": "10.10.1.3" if index % 2 else "10.10.1.4",
                             "description": f"{subject} station {index}", "hostname": f"{subject.lower()}-{index}.lab.test",
                             "changes": changes})
    elif app == "wolf":
        duplicate = index % 5 == 0
        spec.update(task_family_id="wolfcms.validate_page_slug", condition="duplicate_sibling" if duplicate else "own_slug_allowed",
                    fixture={"kind": "duplicate" if duplicate else "edit", "initial_title": f"{subject} notice {index}",
                             "fields": {"title": f"{subject} revised notice {index}"}})
    elif app == "wondercms":
        spec.update(task_family_id="wondercms.reorder_menu", condition="second_up" if index % 5 == 0 else "first_down",
                    names=[f"{subject} Home {index}", f"{subject} Guide {index}"])
    elif app == "dokuwiki":
        spec.update(task_family_id="dokuwiki.media_namespace_acl", condition="namespace_denial_with_exact_id_allow",
                    secret=f"secret{index}", public=f"public{index}", media=f"image{index}.png")
    elif app == "flatpress":
        spec.update(task_family_id="flatpress.follow_prettyurl", condition="emoji_title_with_ascii_control",
                    title=f"{subject} 😀 journal {index}", body=f"{subject} emoji article body number {index}.",
                    ascii_title=f"{subject} ordinary journal {index}", ascii_body=f"{subject} ordinary article body {index}.")
    else:
        spec.update(task_family_id="akaunting.create_product_price", condition="ordinary_amount" if index % 5 == 0 else "localized_large_amount")
    return spec


def run_directory(output, spec, build, calibration=False):
    base = (output.parent / "calibration" if output.name == "collections" else output / "calibration") if calibration else (
        output if output.name == "collections" else output / "collections")
    return base / spec["case_id"] / (spec["instance_id"] + "-" + build)


def metadata(spec, build):
    private = json.loads((ROOT / "cases" / spec["case_id"] / "private.json").read_text())
    return {**{key: spec[key] for key in ("case_id", "category", "split", "instance_id", "task_family_id", "condition")},
            "build": build, "source_commit": private["base_commit" if build == "A" else "reference_commit"]}


def collect(browser, spec, build, directory, port):
    if spec["app"] in {"phpipam", "opencart", "wolf"}:
        return collect_legacy(browser, spec, build, directory, port)
    if spec["app"] == "akaunting":
        return collect_akaunting(browser, spec, build, directory, port)
    return collect_navigation(browser, spec, build, directory, port)


def collect_legacy(browser, spec, build, directory, port):
    directory.parent.mkdir(parents=True, exist_ok=True)
    if spec["app"] == "opencart":
        case, instance_type, authenticate, _, flow = TARGETED_APPS["opencart"]
        TARGETED_APPS["opencart"] = (case, instance_type, authenticate, NumericWeightObserver, flow)
    group = {"app": spec["app"], "id": spec["instance_id"], "parent_scenario_id": spec["instance_id"],
             "family": spec["condition"], "split": spec["split"], "fixture": spec["fixture"]}
    row, result = collect_targeted(browser, directory.parent, group, build, "saved", port, name=directory.name)
    shutil.copyfile(directory / "result.json", directory / "private/legacy-result.json")
    answer = json.loads((directory / "label.json").read_text())
    task = json.loads((directory / "public.json").read_text())["task"]
    return import_trace(directory, metadata(spec, build), task, answer["verdict"], answer["observation"],
                        evidence_steps=answer["evidence_steps"], checks=answer["field_readings"])


class NumericWeightObserver(OpencartObserver):
    def __init__(self, page, fixture):
        super().__init__(page, fixture)
        expected = {**fixture["values"], "weight": str(Decimal(fixture["values"]["weight"]).normalize())}
        self.evidence.expected = json.dumps(expected, ensure_ascii=False, sort_keys=True)


def collect_akaunting(browser, spec, build, directory, port):
    directory.parent.mkdir(parents=True, exist_ok=True)
    group = make_group("akaunting", spec["index"] + 49, "preservation" if spec["index"] % 5 == 0 else "target", "saved", ["A", "B"], False)
    group.update(id=spec["instance_id"], parent_scenario_id=spec["instance_id"], split="test")
    row, result = collect_test(browser, directory.parent, group, build, port)
    produced = directory.parent / row["id"]
    produced.rename(directory)
    shutil.copyfile(directory / "result.json", directory / "private/legacy-result.json")
    answer = json.loads((directory / "label.json").read_text())
    return import_trace(directory, metadata(spec, build), group["task"], answer["verdict"], answer["observation"],
                        evidence_steps=answer["evidence_steps"], checks=answer["field_readings"])


class NamespaceMediaInstance(DokuwikiInstance):
    def seed_media(self, spec):
        for namespace in (spec["secret"], spec["public"]):
            path = self.app / "data/media" / namespace / spec["media"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(PIXEL)
        rules = ["* @ALL 1", f"{spec['secret']}:* @ALL 0", f"{spec['secret']}:{spec['media']} @ALL 1",
                 f"{spec['public']}:* @ALL 1"]
        (self.app / "conf/acl.auth.php").write_text("# <?php exit()?>\n" + "\n".join(rules) + "\n")
        index = (f"====== {spec['subject']} media access ======\n"
                 f"[[{self.url}/lib/exe/detail.php?media={spec['public']}:{spec['media']}|Allowed image detail]]\n\n"
                 f"[[{self.url}/lib/exe/detail.php?media={spec['secret']}:{spec['media']}|Restricted image detail]]\n")
        (self.app / "data/pages/start.txt").write_text(index)


class PrettyUrlInstance(FlatpressInstance):
    router = "dataset26-flatpress-router.php"

    def configure(self):
        (self.app / self.router).write_text("<?php\n$path=parse_url($_SERVER['REQUEST_URI'],PHP_URL_PATH);\n"
            "if ($path!=='/' && is_file(__DIR__.$path)) return false;\n"
            "$_SERVER['SCRIPT_NAME']='/index.php';$_SERVER['SCRIPT_FILENAME']=__DIR__.'/index.php';\n"
            "require __DIR__.'/index.php';\n")

    def seed_posts(self, spec):
        program = ("require 'defaults.php';require INCLUDES_DIR.'includes.php';$fp_config=config_load();"
                   "$s=json_decode(stream_get_contents(STDIN),true);"
                   "$GLOBALS['fp_config']['general']['title']='Dataset26 Journal';config_save();"
                   "$e=['subject'=>$s['title'],'content'=>$s['body'],'author'=>'admin','date'=>1735732800,'categories'=>[]];"
                   "$a=['subject'=>$s['ascii_title'],'content'=>$s['ascii_body'],'author'=>'admin','date'=>1735819200,'categories'=>[]];"
                   "echo json_encode([entry_save($e),entry_save($a)]);")
        with (self.directory / "post-seed.log").open("w") as handle:
            subprocess.run([str(self.runtime / "php"), "-r", program], cwd=self.app, env=self.env,
                           input=json.dumps(spec), text=True, check=True, stdout=handle, stderr=subprocess.STDOUT)
        for path in (self.app / "fp-content/cache").glob("*prettyurls*"):
            path.unlink()


def collect_navigation(browser, spec, build, directory, port):
    directory.mkdir(parents=True)
    klass = {"wondercms": WondercmsInstance, "dokuwiki": NamespaceMediaInstance, "flatpress": PrettyUrlInstance}[spec["app"]]
    instance = klass(ROOT, directory / "private/instance", build, port)
    try:
        instance.start()
        with browser.new_page(viewport=VIEWPORT, device_scale_factor=1, locale="en-US", timezone_id="UTC") as page:
            page.set_default_timeout(10000)
            if spec["app"] == "wondercms":
                return collect_menu(page, instance, spec, build, directory)
            if spec["app"] == "dokuwiki":
                return collect_media(page, instance, spec, build, directory)
            return collect_prettyurl(page, instance, spec, build, directory)
    finally:
        instance.close()


def collect_menu(page, instance, spec, build, directory):
    db_path = instance.app / "data/database.js"
    db = json.loads(db_path.read_text())
    for key, name in zip(("0", "1"), spec["names"]):
        db["config"]["menuItems"][key]["name"] = name
    db_path.write_text(json.dumps(db, ensure_ascii=False))
    fixture = json.loads((ROOT / "cases/sweeper-196/fixtures.private.json").read_text())["target"]
    wondercms_login(page, instance.url, fixture)
    up = spec["condition"] == "second_up"
    task = (f"Open Settings and its Menu tab. The first two menu labels are {spec['names']!r}. "
            + ("Move the second item up." if up else "Move the first item down.")
            + " Reopen Settings and Menu, and verify the first two labels are swapped and retained.")
    recorder = DatasetRecorder(page, directory, metadata(spec, build), task)
    recorder.click(page.locator(".settings.button"))
    recorder.click(page.locator('ul.nav-tabs a[href="#menu"]'))
    before = page.locator('#menuSettings div.editText[data-target="menuItemUpdate"]').all_text_contents()[:2]
    recorder.click(page.locator(f'.menu-item-{"up" if up else "down"}[data-menu="{"1" if up else "0"}"]'))
    recorder.click(page.locator(".settings.button"))
    recorder.click(page.locator('ul.nav-tabs a[href="#menu"]'))
    after = [value.strip() for value in page.locator('#menuSettings div.editText[data-target="menuItemUpdate"]').all_text_contents()[:2]]
    expected = list(reversed(spec["names"]))
    verdict = "pass" if after == expected else "fail"
    checks = [{"field": "Menu order", "before": before, "after": after, "expected": expected, "step": recorder.index}]
    write_json(directory / "private/menu-readings.json", checks)
    return recorder.finish(verdict, f"After reopening Menu, expected {expected!r}; visible order is {after!r}.", checks=checks)


def collect_media(page, instance, spec, build, directory):
    instance.seed_media(spec)
    home = instance.url + "/doku.php?id=start"
    page.goto(home, wait_until="domcontentloaded")
    settle(page)
    task = (f"On the {spec['subject']} media-access page, open Allowed image detail and confirm it is readable. "
            f"Return and open Restricted image detail ({spec['secret']}:{spec['media']}). Its namespace denies read, "
            "so Permission Denied must appear and the viewable media-detail section must be absent, including after reload.")
    recorder = DatasetRecorder(page, directory, metadata(spec, build), task)
    recorder.click(page.get_by_role("link", name="Allowed image detail", exact=True))
    allowed = page.locator(".img_detail").count() > 0 and "Permission Denied" not in page.locator("body").inner_text()
    allowed_step = recorder.index
    recorder.goto(home)
    recorder.click(page.get_by_role("link", name="Restricted image detail", exact=True))
    recorder.press("Control+r")
    first_denied = "Permission Denied" in page.locator("body").inner_text()
    recorder.goto(home)
    recorder.click(page.get_by_role("link", name="Restricted image detail", exact=True))
    body = page.locator("body").inner_text()
    denied = "Permission Denied" in body
    readable = page.locator(".img_detail").count() > 0
    if not denied and not readable:
        raise RuntimeError("media detail rendered neither identifiable denied state nor readable media UI")
    verdict = "pass" if allowed and first_denied and denied and not readable else "fail"
    checks = [{"field": "Allowed media readable", "actual": allowed, "expected": True, "step": allowed_step},
              {"field": "Restricted media denied", "actual": denied, "expected": True, "step": recorder.index},
              {"field": "Restricted media detail visible", "actual": readable, "expected": False, "step": recorder.index}]
    write_json(directory / "private/acl-readings.json", {"checks": checks, "visible_text": body})
    return recorder.finish(verdict, f"Allowed media readable={allowed}; restricted page Permission Denied={denied}; readable detail section={readable}.",
                           evidence_steps=[allowed_step, recorder.index], checks=checks)


def collect_prettyurl(page, instance, spec, build, directory):
    instance.seed_posts(spec)
    home = instance.url + "/"
    page.goto(home, wait_until="domcontentloaded")
    settle(page)
    task = (f"On the blog home page, follow the ordinary article title {spec['ascii_title']!r} and verify its body {spec['ascii_body']!r}. "
            f"Return home and follow the emoji article title {spec['title']!r}; it must show body {spec['body']!r} after reload, "
            "without a page-not-found message.")
    recorder = DatasetRecorder(page, directory, metadata(spec, build), task)
    recorder.click(page.get_by_role("link", name=spec["ascii_title"], exact=True).first)
    ascii_ok = spec["ascii_body"] in page.locator("body").inner_text()
    ascii_step = recorder.index
    recorder.goto(home)
    recorder.click(page.get_by_role("link", name=spec["title"], exact=True).first)
    recorder.press("Control+r")
    recorder.goto(home)
    recorder.click(page.get_by_role("link", name=spec["title"], exact=True).first)
    text = page.locator("body").inner_text()
    missing = "Sorry, we could not find the page you requested" in text
    found = spec["body"] in text
    if not missing and not found:
        raise RuntimeError("emoji title link reached neither identifiable article nor FlatPress not-found page")
    verdict = "pass" if ascii_ok and found and not missing else "fail"
    checks = [{"field": "Ordinary article body", "expected": spec["ascii_body"], "actual": ascii_ok, "step": ascii_step},
              {"field": "Emoji article body", "expected": spec["body"], "actual": found, "step": recorder.index},
              {"field": "Page not found", "expected": False, "actual": missing, "step": recorder.index}]
    write_json(directory / "private/navigation-readings.json", {"checks": checks, "visible_text": text})
    return recorder.finish(verdict, f"Ordinary article resolves={ascii_ok}; emoji article body visible={found}; FlatPress not-found message={missing}.",
                           evidence_steps=[ascii_step, recorder.index], checks=checks)


if __name__ == "__main__":
    main()
