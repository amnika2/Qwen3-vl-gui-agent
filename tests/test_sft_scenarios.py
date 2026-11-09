import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from playwright.sync_api import sync_playwright

from guiqa.oracle import judge
from guiqa.sft_scenarios import SupplementaryObserver, load_scenarios, scenario_flow


def test_catalog_has_distinct_whole_scenario_partitions_and_bounded_flows():
    scenarios = load_scenarios(Path(__file__).resolve().parents[1] / "configs/sft-supplementary-scenarios.yaml")
    assert len(scenarios) == len({s["fingerprint"] for s in scenarios}) == 24
    train = {s["id"] for s in scenarios if s["data_split"] == "train"}
    validation = {s["id"] for s in scenarios if s["data_split"] == "validation"}
    assert len(train) == 20 and len(validation) == 4 and not train & validation
    assert all(len(scenario_flow(s["fixture"]).steps) <= 30 for s in scenarios)


@pytest.mark.parametrize("wrong_object,wrong_subject,expected", [
    (False, False, "pass"), (True, False, "inconclusive"), (False, True, "fail"),
])
@pytest.mark.parametrize("body", ["New body", "First line\nSecond line"])
def test_complete_reopened_fields_and_same_object_are_required(wrong_object, wrong_subject, expected, body):
    fixture = {"is_wiki": False, "page_id": "training-contact", "selector": "#content",
        "initial": "Old body", "initial_subject": "Old title",
        "stages": [{"content": body, "subject": "New title"}],
        "expected": json.dumps({"content": body, "subject": "New title"}, sort_keys=True)}
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.route("http://guiqa.test/**", lambda route: route.fulfill(content_type="text/html",
                body='<input id="subject" value="Old title"><textarea id="content">Old body</textarea>'))
            page.goto("http://guiqa.test/admin.php?p=static&action=write&page=training-contact")
            observer = SupplementaryObserver(page, fixture)
            observer.capture(0, "initial.png")
            fields = {"save": "Save", "oldid": fixture["page_id"], "id": fixture["page_id"],
                      "subject": "New title", "content": body.replace("\n", "\r\n")}
            observer._response(SimpleNamespace(url="http://guiqa.test/admin.php", status=302,
                request=SimpleNamespace(method="POST", headers={"content-type": "application/x-www-form-urlencoded"},
                                        post_data=urlencode(fields))))
            target = "other-page" if wrong_object else fixture["page_id"]
            page.goto(f"http://guiqa.test/admin.php?p=static&action=write&page={target}")
            page.locator("#content").fill(body)
            page.locator("#subject").fill("Old title" if wrong_subject else "New title")
            observer.capture(2, "reopened.png")
            assert judge(observer.evidence)[0] == expected


def test_second_save_without_first_observation_cannot_pass():
    fixture = {"is_wiki": True, "page_id": "training:policy", "selector": "#content", "initial": "Old",
               "stages": [{"content": "Draft"}, {"content": "Final"}],
               "expected": json.dumps({"content": "Final"})}
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            page = browser.new_page()
            page.route("http://guiqa.test/**", lambda route: route.fulfill(
                content_type="text/html", body='<textarea id="content">Old</textarea>'))
            page.goto("http://guiqa.test/doku.php?id=training:policy&do=edit")
            observer = SupplementaryObserver(page, fixture)
            observer.capture(0, "initial.png")
            fields = {"do[save]": "Save", "id": fixture["page_id"], "wikitext": "Final"}
            observer._response(SimpleNamespace(url="http://guiqa.test/doku.php", status=302,
                request=SimpleNamespace(method="POST", headers={"content-type": "application/x-www-form-urlencoded"},
                                        post_data=urlencode(fields))))
            page.goto("http://guiqa.test/doku.php?id=training:policy&do=edit")
            page.locator("#content").fill("Final")
            observer.capture(2, "final.png")
            assert judge(observer.evidence)[0] == "inconclusive"
