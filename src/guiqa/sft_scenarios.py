"""Varied, observable form-edit scenarios for the two supplementary apps."""
import json
import re
import time
from dataclasses import asdict
from urllib.parse import parse_qs, urlparse

import yaml

from .artifacts import digest
from .oracle import Evidence, in_view, request_fields
from .sft_flows import SftFlow, click_at, click_locator, press, scroll_to, type_text


def load_scenarios(path):
    entries = yaml.safe_load(path.read_text())["scenarios"]
    scenarios = [prepare_scenario(entry) for entry in entries]
    if len({scenario["id"] for scenario in scenarios}) != len(scenarios):
        raise ValueError("scenario IDs must be unique")
    return scenarios


def prepare_scenario(entry):
    case = entry["case_id"]
    is_wiki = case == "sweeper-034"
    if case not in {"sweeper-034", "sweeper-053"} or not re.fullmatch(r"[a-z0-9-]+", entry["id"]):
        raise ValueError("supplementary scenarios require a supported app and a lowercase ID")
    if (entry["data_split"] not in {"train", "validation"} or entry["build"] not in {"A", "B"}
            or entry["workflow"] not in {"target", "preservation"}):
        raise ValueError(f"{entry['id']}: invalid split, build, or workflow")
    mode = entry["edit"]
    if mode not in {"replace", "append", "replace_suffix", "keep"}:
        raise ValueError(f"{entry['id']}: unknown edit operation")
    if entry["start"] not in {"editor", "view" if is_wiki else "list"}:
        raise ValueError(f"{entry['id']}: invalid start page")
    initial, text = entry["initial"], entry["input"]
    if not isinstance(initial, str) or not isinstance(text, str) or len(text) > 2000:
        raise ValueError(f"{entry['id']}: input must fit the action protocol")
    if mode == "replace_suffix" and (not entry["old_suffix"] or not initial.endswith(entry["old_suffix"])):
        raise ValueError(f"{entry['id']}: suffix does not match initial text")
    body = {"replace": text, "append": initial + text, "keep": initial}.get(mode)
    if mode == "replace_suffix":
        body = initial[:-len(entry["old_suffix"])] + text
    subject = entry.get("subject", entry.get("initial_subject"))
    if not is_wiki and (not entry["initial_subject"] or not subject):
        raise ValueError(f"{entry['id']}: FlatPress requires page subjects")
    if mode == "keep" and (is_wiki or subject == entry["initial_subject"]):
        raise ValueError(f"{entry['id']}: keeping the body must change another field")
    if "intermediate" in entry and mode != "replace":
        raise ValueError(f"{entry['id']}: two-save scenarios use replacement edits")
    fields = {"content": body}
    if not is_wiki:
        fields["subject"] = subject
    stages = [{**fields, "content": entry["intermediate"]}] if "intermediate" in entry else []
    stages.append(fields)
    page_id = ("training:" if is_wiki else "training-") + entry["id"]
    fixture = {**entry, "scenario_id": entry["id"], "page_id": page_id,
               "selector": "textarea[name=wikitext]" if is_wiki else "textarea[name=content]",
               "is_wiki": is_wiki, "stages": stages, "matrix_expected": "pass",
               "expected": json.dumps(fields, ensure_ascii=False, sort_keys=True)}
    task = scenario_task(fixture)
    fingerprint = digest({key: value for key, value in fixture.items() if key not in {
        "id", "scenario_id", "page_id", "data_split", "build", "workflow", "family"}})
    return {"id": entry["id"], "case_id": case, "build": entry["build"], "workflow": entry["workflow"],
            "family": entry["family"], "data_split": entry["data_split"], "fingerprint": fingerprint,
            "fixture": fixture, "public": {"task": task, "expected": fixture["expected"]}}


def scenario_task(fixture):
    app = "DokuWiki" if fixture["is_wiki"] else "FlatPress"
    pieces = [f"Edit {app} page {fixture['page_id']}."]
    if fixture["start"] == "view":
        pieces.append("Start from the displayed page and open Edit this page.")
    elif fixture["start"] == "list":
        pieces.append(f"From Statics, open the page titled {fixture['initial_subject']!r}.")
    text = json.dumps(fixture["input"], ensure_ascii=False)
    if fixture["edit"] == "append":
        pieces.append(f"Append {text} at the end of Content, preserving all existing text.")
    elif fixture["edit"] == "replace_suffix":
        pieces.append(f"Replace only the ending {fixture['old_suffix']!r} with {text}; preserve the preceding text.")
    elif fixture["edit"] == "keep":
        pieces.append("Keep Content unchanged.")
    else:
        if "intermediate" in fixture:
            pieces.append(f"First replace Content with {json.dumps(fixture['intermediate'], ensure_ascii=False)}, "
                          "save, then reopen this same editor and confirm the saved text before the next edit.")
        pieces.append(f"Replace the entire Content with {text}.")
    if "subject" in fixture:
        order = "before editing Content" if fixture.get("field_order") == "subject_first" else "after editing Content"
        pieces.append(f"Set Subject to {fixture['subject']!r} {order}.")
    if "summary" in fixture:
        pieces.append(f"Set Edit summary to {fixture['summary']!r} on each save.")
    if fixture.get("minor"):
        pieces.append("Mark Minor Changes on each save.")
    if fixture.get("preview"):
        pieces.append("Use Preview to inspect the draft, then submit Save.")
    else:
        pieces.append("Submit Save.")
    pieces.append("Reopen this same page editor after saving. Inspect the complete Content"
                  + (" and Subject" if not fixture["is_wiki"] else "")
                  + ". Report pass when the saved values match the requested edits, fail when a completed "
                    "save reopens with different values, or blocked when you cannot complete the check.")
    return " ".join(pieces)


def scenario_flow(fixture):
    wiki = fixture["is_wiki"]
    body_selector = fixture["selector"]
    page_id = fixture["page_id"]
    edit_selector = "a[href*='do=edit']" if wiki else f"a[href*='action=write'][href*='page={page_id}']"
    open_editor = click_locator(lambda page: page.locator(edit_selector).first)
    steps, review_steps, navigation_steps = [], [0], []
    if fixture["start"] != "editor":
        steps.append(open_editor)
        navigation_steps.append(len(steps))
        review_steps.append(len(steps))
    for index, stage in enumerate(fixture["stages"]):
        fields = ["content"] if wiki or "subject" not in fixture else (
            ["subject", "content"] if fixture.get("field_order") == "subject_first" else ["content", "subject"])
        for field in fields:
            if field == "content" and fixture["edit"] == "keep":
                continue
            selector = body_selector if field == "content" else "#subject"
            if not wiki:
                steps.append(scroll_to(selector))
            steps.extend([click_at(selector), press("Control+a")])
            mode = fixture["edit"] if field == "content" else "replace"
            if mode in {"append", "replace_suffix"}:
                steps.append(press("ArrowRight"))
            if mode == "replace_suffix":
                steps.extend(press("Backspace") for _ in fixture["old_suffix"])
            text = fixture["input"] if mode in {"append", "replace_suffix"} else stage[field]
            steps.append(type_text(text))
            review_steps.append(len(steps))
        if "summary" in fixture:
            steps.extend([click_at("#edit__summary"), press("Control+a"), type_text(fixture["summary"])])
            review_steps.append(len(steps))
        if fixture.get("minor"):
            steps.append(click_at("#edit__minoredit"))
        input_step = len(steps)
        if fixture.get("preview"):
            if not wiki:
                steps.append(scroll_to("#preview"))
            steps.append(click_at("#edbtn__preview" if wiki else "#preview"))
            navigation_steps.append(len(steps))
            review_steps.append(len(steps))
        if not wiki or fixture.get("preview"):
            steps.append(scroll_to("#edbtn__save" if wiki else "#save"))
        steps.append(click_at("#edbtn__save" if wiki else "#save"))
        navigation_steps.append(len(steps))
        submit_step = len(steps)
        steps.append(open_editor)
        navigation_steps.append(len(steps))
        review_steps.append(len(steps))
        if not wiki:
            steps.extend([scroll_to("#subject"), scroll_to(body_selector)])
            review_steps.extend([len(steps) - 1, len(steps)])
        if index < len(fixture["stages"]) - 1:
            review_steps.append(len(steps))
    if len(steps) > 30:
        raise ValueError(f"{fixture['scenario_id']}: flow exceeds the inference action budget")
    return SftFlow(steps, input_step, submit_step, tuple(sorted(set(review_steps))), tuple(navigation_steps))


class SupplementaryObserver:
    def __init__(self, page, fixture):
        self.page, self.fixture = page, fixture
        self.evidence = Evidence(expected=fixture["expected"])
        self.step = 0
        self.observed_stages = 0
        self.pending_stage = None
        self.submitted_at = None
        self.reopened_at = None
        self.visible_fields = {}
        self.stage_submission_valid = False
        page.on("response", self._response)

    def capture(self, step, screenshot):
        self.step = step
        url = urlparse(self.page.url)
        query = parse_qs(url.query)
        wiki = self.fixture["is_wiki"]
        editor = (url.path == "/doku.php" and query.get("id") == [self.fixture["page_id"]]
                  and query.get("do") == ["edit"]) if wiki else (
                      url.path == "/admin.php" and query.get("p") == ["static"]
                      and query.get("action") == ["write"] and query.get("page") == [self.fixture["page_id"]])
        if not editor:
            return
        selectors = {"content": self.fixture["selector"]}
        if not wiki:
            selectors["subject"] = "#subject"
        if self.submitted_at is None:
            initial = {"content": self.fixture["initial"]}
            if not wiki:
                initial["subject"] = self.fixture["initial_subject"]
            actual = {field: self.page.locator(selector).input_value() for field, selector in selectors.items()}
            if actual == initial and not self.evidence.preconditions:
                self.evidence.preconditions = True
                self.evidence.events.append({"kind": "initial_editor", "step": step,
                    "object_id": self.fixture["page_id"], "values": actual, "screenshot": screenshot})
            return
        if self.pending_stage is None or self.reopened_at is None or self.reopened_at <= self.submitted_at:
            return
        for field, selector in selectors.items():
            locator = self.page.locator(selector)
            if in_view(locator):
                self.visible_fields[field] = locator.input_value()
                self.evidence.events.append({"kind": "reopened_field", "step": step,
                    "object_id": self.fixture["page_id"], "stage": self.pending_stage,
                    "field": field, "value": self.visible_fields[field], "screenshot": screenshot})
        if self.visible_fields.keys() != selectors.keys():
            return
        actual = json.dumps(self.visible_fields, ensure_ascii=False, sort_keys=True)
        expected = json.dumps(self.fixture["stages"][self.pending_stage], ensure_ascii=False, sort_keys=True)
        self.evidence.events.append({"kind": "reopened_stage", "stage": self.pending_stage, "step": step,
            "object_id": self.fixture["page_id"], "actual": actual, "expected": expected, "screenshot": screenshot})
        if self.pending_stage == len(self.fixture["stages"]) - 1:
            self.evidence.actual = actual
            self.evidence.reopened = self.evidence.same_object = True
        elif actual != expected or not self.stage_submission_valid:
            self.evidence.preconditions = False
        self.observed_stages += 1
        self.pending_stage = None

    def _response(self, response):
        request = response.request
        url = urlparse(response.url)
        query = parse_qs(url.query)
        wiki = self.fixture["is_wiki"]
        if url.path != ("/doku.php" if wiki else "/admin.php"):
            return
        now = time.monotonic()
        if request.method == "GET" and request.is_navigation_request() and self.submitted_at is not None:
            self.reopened_at = now
        if request.method != "POST":
            return
        fields = request_fields(request)
        if ("do[save]" if wiki else "save") not in fields:
            return
        if self.observed_stages >= len(self.fixture["stages"]):
            return
        page_id = fields.get("id") if wiki else fields.get("oldid")
        stage = self.fixture["stages"][self.observed_stages]
        # Native form serialization uses CRLF; textarea.value exposes LF.
        content = fields.get("wikitext" if wiki else "content", "")
        actual = {"content": content.replace("\r\n", "\n")}
        if not wiki:
            actual["subject"] = fields.get("subject")
        correct = actual == stage and page_id == self.fixture["page_id"]
        if not wiki:
            correct = correct and fields.get("id") == self.fixture["page_id"]
        if "summary" in self.fixture:
            correct = correct and fields.get("summary") == self.fixture["summary"]
        if self.fixture.get("minor"):
            correct = correct and fields.get("minor") == "1"
        self.submitted_at, self.reopened_at = now, None
        self.pending_stage, self.visible_fields = self.observed_stages, {}
        self.evidence.submitted = correct
        self.evidence.response_valid = response.status == 302
        self.stage_submission_valid = correct and self.evidence.response_valid
        self.evidence.reopened = self.evidence.same_object = False
        self.evidence.actual = None
        self.evidence.events.append({"kind": "submit_response", "step": self.step + 1,
            "stage": self.pending_stage, "object_id": page_id, "submitted_values": actual,
            "transport_newlines": "CRLF" if "\r\n" in content else "LF",
            "correct_input": correct, "status": response.status, "accepted": response.status == 302,
            "elapsed_marker": now})

    def data(self):
        return asdict(self.evidence)
