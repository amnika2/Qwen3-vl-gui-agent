import json
import os
import shlex
import socket
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, NamedTuple
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout

from .actions import parse_action
from .artifacts import digest, source_digest, write_json
from .browser import VIEWPORT, akaunting_login, appointments_login, execute, login, settle
from .native import AkauntingInstance, AppointmentsInstance, EmlogInstance
from .ledger import ProgressLedger
from .control import VISIBLE_CHANGE, LoopControl, review_guard
from .extra_instances import AmpacheInstance, AnchorInstance
from .extra_cases import (PlaylistObserver, VariableObserver, ampache_login, anchor_login,
                          playlist_reference, variable_reference)
from .oracle import ItemObserver, ProfileObserver, ProviderObserver, judge, parse_usd
from .report import render
from .review_evidence import parse_review, select_evidence
from . import sft_data, sft_flows
from .training_instances import (DokuwikiInstance, FlatpressInstance, PluxmlInstance, TypesetterInstance,
                                 WondercmsInstance)
from .training_cases import (DokuwikiObserver, FlatpressObserver, PluxmlObserver, TypesetterObserver,
                             WondercmsObserver, dokuwiki_login, dokuwiki_reference, flatpress_login,
                             flatpress_reference, pluxml_login, pluxml_reference,
                             typesetter_login, typesetter_reference, wondercms_login, wondercms_reference)
from .sft_scenarios import SupplementaryObserver

IMPLEMENTATION_HASH = source_digest(Path(__file__).resolve().parents[2])


def run_case(root, case_id, build, workflow, method, config, port=13200, model=None, repeat=0, experiment="pilot", scenario=None,
             on_started=None):
    if method == "sft" and config.get("review_format") in {"evidence_v1", "evidence_v2", "evidence_facts_v1"}:
        raise ValueError("collect raw SFT with configs/agent.yaml, then supply visible field annotations to scripts/build_review_evidence.py")
    public, fixture = load_case(root, case_id, workflow)
    adapter = ADAPTERS[case_id]
    if scenario is not None:
        public = scenario["public"]
        fixture = {**fixture, **scenario["fixture"]}
    if method == "sft" and case_id not in sft_flows.FLOWS:
        raise ValueError(f"{case_id}: SFT collection is not enabled; see configs/sft-split.yaml")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    directory = root / "runs" / run_id
    directory.mkdir(parents=True)
    run_config = {**config, "case_id": case_id, "workflow": workflow, "method": method,
                  "public": public, "viewport": VIEWPORT, "dpr": 1,
                  "implementation_hash": IMPLEMENTATION_HASH}
    config_hash = digest(run_config)
    if adapter.review_profile != "persistence":
        run_config["review_profile"] = adapter.review_profile
        config_hash = digest(run_config)
    write_json(directory / "manifest.json", {"run_id": run_id, "config": run_config, "config_hash": config_hash,
               "experiment": experiment, "hostname": socket.gethostname(),
               "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES")})
    matrix_expected = fixture.get("matrix_expected", "fail" if build == "A" and workflow == "target" else "pass")
    write_json(directory / "private/manifest.json", {"build": build, "repeat": repeat, "expected_oracle": matrix_expected})
    result = {"run_id": run_id, "case_id": case_id, "workflow": workflow, "method": method,
              "build": build,
              "experiment": experiment, "repeat": repeat, "attempt_id": 1,
              "task": public["task"], "expected": public["expected"], "matrix_expected": matrix_expected, "actual": None,
              "agent_verdict": "blocked", "oracle_verdict": "inconclusive", "actions": 0,
              "model_calls": 0, "config_hash": config_hash, "model_revision": config.get("model_revision"),
              "termination_reason": "running", "started": True,
              "reproduce": f"python -m guiqa run --case {case_id} --build {build} --workflow {workflow} --method {method}"}
    if config.get("review_format") in {"evidence_v1", "evidence_v2", "evidence_facts_v1"}:
        result["review_format"] = config["review_format"]
        write_json(directory / "agent-config.json", config)
        result["reproduce"] += " --config " + shlex.quote(str(directory / "agent-config.json"))
    if scenario is not None:
        result.update(scenario_id=scenario["id"], scenario_family=scenario["family"],
                      scenario_fingerprint=scenario["fingerprint"], data_split=scenario["data_split"])
        write_json(directory / "private/scenario.json", scenario)
    start = time.monotonic()
    if adapter.review_profile != "persistence":
        result["review_profile"] = adapter.review_profile
    instance = adapter.instance(root, directory / "private/instance", build, port)
    observer = None
    write_json(directory / "result.json", {**result, "elapsed_seconds": 0})
    if on_started is not None:
        on_started(directory)
    try:
        instance.start()
        if scenario is not None:
            instance.seed_training_page(fixture)
        with sync_playwright() as playwright:
            with playwright.chromium.launch() as browser:
                page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1, locale="en-US", timezone_id="UTC")
                result["browser_version"] = browser.version
                page.set_default_timeout(10000)
                adapter.login(page, instance.url, fixture)
                observer_type = adapter.scenario_observer if scenario is not None and adapter.scenario_observer else adapter.observer
                observer = observer_type(page, fixture)
                context = RunContext(directory, result, observer, page)
                context.capture("Authenticated initial profile")
                if method == "script":
                    adapter.reference(context, fixture)
                elif method == "sft":
                    flow = sft_flows.FLOWS[case_id](fixture, build)
                    sft_data.sft_collect(context, public["task"], flow, config, reviewer=adapter.reviewer)
                else:
                    agent(context, public["task"], model, config)
                verdict, reason = adapter.oracle(observer) if adapter.oracle else judge(observer.evidence)
                result.update(oracle_verdict=verdict, oracle_reason=reason, actual=observer.evidence.actual)
    except KeyboardInterrupt:
        result["termination_reason"] = "interrupted"
    except Exception as error:
        result.update(oracle_verdict="infra_error", termination_reason="infrastructure_failure",
                      error=f"{type(error).__name__}; see private/error.txt")
        (directory / "private/error.txt").write_text(traceback.format_exc())
        if observer:
            observer.evidence.healthy = False
    finally:
        instance.close()
        if observer:
            write_json(directory / "private/oracle.json", observer.data())
        if model is not None:
            result["gpu"] = model.metrics()
        result["elapsed_seconds"] = time.monotonic() - start
        write_json(directory / "result.json", result)
        render(directory)
    return directory, result


def load_case(root: Path, case_id, workflow):
    if case_id not in ADAPTERS and case_id in APP_MODULES:
        from importlib import import_module
        import_module(APP_MODULES[case_id], __package__).register()
    if case_id not in ADAPTERS:
        raise ValueError(f"{case_id}: not yet calibrated/deployable; see docs/STATUS.md")
    public = json.loads((root / "cases" / case_id / "public.json").read_text())["workflows"][workflow]
    fixture = json.loads((root / "cases" / case_id / "fixtures.private.json").read_text())[workflow]
    return public, fixture


class RunContext:
    def __init__(self, directory, result, observer, page):
        self.directory = directory
        self.result = result
        self.observer = observer
        self.page = page
        self.history = []
        self.tools = None
        self.agent_session = None

    def execute(self, action, caller="agent"):
        return self.tools.execute(action, caller) if self.tools else execute(self.page, action)

    def capture(self, description, action=None, raw=None, elapsed=0, error=None):
        step = self.result["actions"]
        name = f"screenshots/{step:03d}.png"
        screenshot = self.directory / name
        screenshot.parent.mkdir(exist_ok=True)
        # Wait across navigation commits until the complete document has painted.
        self.page.wait_for_function("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(document.readyState === 'complete'))))")
        self.page.screenshot(path=str(screenshot))
        self.observer.capture(step, name)
        row = {"run_id": self.result["run_id"], "case_id": self.result["case_id"],
               "workflow": self.result["workflow"], "step": step, "screenshot": name,
               "description": description, "action": action, "raw_output": raw,
               "elapsed_seconds": elapsed, "execution_error": error,
               "screenshot_size": [VIEWPORT["width"], VIEWPORT["height"]]}
        with (self.directory / "trace.jsonl").open("a") as file:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
            file.flush()
        self.history.append({"step": step, "action": action, "result": error or "executed"})
        write_json(self.directory / "result.json", {**self.result, "elapsed_seconds": elapsed})
        return screenshot

    def event(self, value):
        with (self.directory / "model.jsonl").open("a") as file:
            file.write(json.dumps(value, ensure_ascii=False) + "\n")


def reference(context, fixture):
    page = context.page
    start = time.monotonic()
    page.locator("input[name=name]").fill(fixture["expected"])
    context.result["actions"] += 1
    context.capture("Replace Nickname", {"action": "fill_nickname"}, elapsed=time.monotonic() - start)
    with page.expect_response(lambda response: "blogger.php?action=update" in response.url):
        page.locator("#submit_form").click()
    settle(page)
    context.result["actions"] += 1
    context.capture("Submit profile form", {"action": "save"}, elapsed=time.monotonic() - start)
    page.reload(wait_until="networkidle")
    context.result["actions"] += 1
    context.capture("Reopen the same profile", {"action": "reload"}, elapsed=time.monotonic() - start)
    actual = page.locator("input[name=name]").input_value()
    context.result.update(agent_verdict="pass" if actual == fixture["expected"] else "fail",
                          termination_reason="script_completed", observation=actual, evidence_steps=[3])


def appointments_reference(context, fixture):
    page = context.page
    start = time.monotonic()
    page.locator(".provider-row").filter(has_text="jane@pippins.test").click()
    page.locator(fixture["selector"]).scroll_into_view_if_needed()
    context.result["actions"] += 1
    context.capture("Open Jane Doe's provider details", {"action": "select_provider"}, elapsed=time.monotonic() - start)
    page.locator(fixture["selector"]).fill(fixture["expected"])
    context.result["actions"] += 1
    context.capture("Replace " + fixture["field"], {"action": "fill_provider_field"}, elapsed=time.monotonic() - start)
    with page.expect_response(lambda response: "/providers/update" in response.url):
        page.locator("#save-provider").click()
    settle(page)
    context.result["actions"] += 1
    context.capture("Submit provider update", {"action": "save_provider"}, elapsed=time.monotonic() - start)
    page.reload(wait_until="networkidle")
    page.locator(".provider-row").filter(has_text="jane@pippins.test").click()
    page.locator(fixture["selector"]).scroll_into_view_if_needed()
    context.result["actions"] += 2
    context.capture("Reopen Jane Doe after server reload", {"action": "reload_and_select"}, elapsed=time.monotonic() - start)
    actual = page.locator(fixture["selector"]).input_value()
    context.result.update(agent_verdict="pass" if actual == fixture["expected"] else "fail",
                         termination_reason="script_completed", observation=actual, evidence_steps=[5])


def item_reference(context, fixture):
    page = context.page
    start = time.monotonic()
    for name, value in (("name", fixture["name"]), ("sale_price", fixture["input_amount"]), ("purchase_price", "100")):
        page.locator(f"input[name={name}]").fill(value)
        context.result["actions"] += 1
        context.capture("Fill " + name, {"action": "fill_item_field", "field": name}, elapsed=time.monotonic() - start)
    with page.expect_response(lambda response: urlparse(response.url).path == "/1/common/items" and response.request.method == "POST"):
        page.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_url("**/1/common/items", wait_until="domcontentloaded")
    settle(page)
    context.result["actions"] += 1
    context.capture("Open saved items list", {"action": "save_item"}, elapsed=time.monotonic() - start)
    row = page.locator("tr[data-table-list]").filter(has_text=fixture["name"])
    price = row.locator("td").nth(4).locator("[data-truncate-marquee]").first
    actual = str(parse_usd(price.inner_text()))
    context.result.update(agent_verdict="pass" if actual == fixture["expected"] else "fail",
                         termination_reason="script_completed", observation=actual, evidence_steps=[4])


def agent(context, task, model, config):
    model.begin_run()
    deadline = time.monotonic() + config["wall_seconds"]
    if config.get("agent_runtime", {}).get("enabled"):
        from .agent_session import AgentSession

        with AgentSession(context, task, model, config, deadline) as session:
            context.agent_session = session
            session.start()
            agent_loop(context, task, model, config, deadline)
            session.finish()
    else:
        agent_loop(context, task, model, config, deadline)


def agent_loop(context, task, model, config, deadline):
    if config.get("review_format") in {"evidence_v1", "evidence_v2", "evidence_facts_v1"}:
        context.result["review_format"] = config["review_format"]
    profile = context.result.get("review_profile", "persistence")
    session = context.agent_session
    available = {0}
    ledger = ProgressLedger() if session or context.result["method"] == "qwen-improved" else None
    control = LoopControl(context.directory / "screenshots/000.png") if context.result["method"] == "qwen-controlled" else None
    while time.monotonic() < deadline:
        action = None
        for retry in range(2):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            start = time.monotonic()
            options = {"progress": ledger.snapshot() if ledger else None}
            if session:
                options["progress"] = {**session.progress(), "action_progress": ledger.snapshot()}
            if profile != "persistence":
                options["profile"] = profile
            raw = model.generate(task, context.directory / f"screenshots/{context.result['actions']:03d}.png",
                                 context.history, remaining, **options)
            context.result["model_calls"] += 1
            try:
                action = parse_action(raw, available, normalize_reload=control is not None,
                                      tools=session.tools.catalog if session else None)
            except (ValueError, KeyError, TypeError) as error:
                context.event({"step": context.result["actions"], "retry": retry, "raw_output": raw,
                               "parse_error": str(error), "elapsed_seconds": time.monotonic() - start})
                feedback = "Invalid action: " + str(error)
                if control:
                    feedback += " Return exactly one JSON object for one next action, not a plan or sequence; do not repeat the previous action."
                context.history.append({"step": context.result["actions"], "result": feedback})
                continue
            context.event({"step": context.result["actions"], "retry": retry, "raw_output": raw,
                           "action": action, "elapsed_seconds": time.monotonic() - start})
            break
        if time.monotonic() >= deadline:
            break
        if action is None:
            context.result["termination_reason"] = "invalid_output_twice"
            return
        if action["action"] == "tool":
            if session.state["external_tool_calls"] >= session.settings["max_tool_calls"]:
                context.result["termination_reason"] = "tool_budget"
                return
            session.call_tool(action)
            continue
        if action["action"] == "finish":
            if session and session.defer_finish(action):
                continue
            if time.monotonic() >= deadline:
                break
            if control:
                controlled_review(context, task, model, control, available, deadline,
                                  "model_proposed_finish", action["evidence_steps"])
                return
            context.result.update(agent_verdict=action["verdict"], observation=action["observation"],
                                  evidence_steps=action["evidence_steps"], termination_reason="model_finished")
            return
        if context.result["actions"] >= config["max_actions"]:
            context.result["termination_reason"] = "action_budget"
            return
        execution_error = None
        try:
            outcome = context.execute(action)
            action = {**action, **outcome}
        except BrowserTimeout as error:
            execution_error = str(error)
        context.result["actions"] += 1
        step = context.result["actions"]
        if ledger and execution_error is None:
            ledger.record(action, step)
            context.event({"step": step, "progress": ledger.snapshot()})
        available.add(step)
        screenshot = context.capture(action["action"], action, raw=raw, elapsed=time.monotonic() - start, error=execution_error)
        reason = None
        if control:
            reason = control.record(action, step, screenshot)
            feedback = action_effect_feedback(action, control.difference) if execution_error is None else None
            if feedback:
                context.history[-1]["result"] = "executed; " + feedback
                context.event({"step": step, "effect": "no_visible_change", "difference": control.difference,
                               "feedback": feedback})
        if session:
            session.after_action(execution_error)
            if reason:
                session.refresh(reason)
                control.actions.clear()
                control.static_clicks = 0
                reason = None
        if time.monotonic() >= deadline:
            break
        if control and (reason or step == config["max_actions"] - 1):
            controlled_review(context, task, model, control, available, deadline, reason or "reserved_review_step")
            return
    context.result["termination_reason"] = "wall_clock_budget"


def action_effect_feedback(action, difference):
    if difference is None or action["action"] not in VISIBLE_CHANGE:
        return None
    if difference >= VISIBLE_CHANGE[action["action"]]:
        return None
    if action["action"] == "type":
        return ("The typed text is not visible on the page. Click the target input field, "
                "press Control+a, then type the value.")
    return None


def controlled_review(context, task, model, control, available, deadline, reason, nominated_steps=None):
    before = context.result["actions"]
    profile = context.result.get("review_profile", "persistence")
    context.result.update(control_trigger=reason, intervention_step=before, operation_actions=before)
    context.event({"step": before, "phase": "review", "trigger": reason})
    if profile == "persistence":
        action = {"action": "press", "key": "Control+r"}
        outcome = context.execute(action, caller="controller")
        context.result["actions"] += 1
        context.capture("Controller reload before final judgment", {**action, **outcome})
    after = context.result["actions"]
    available.add(after)
    review_format = context.result.get("review_format")
    organized = review_format in {"evidence_v1", "evidence_v2", "evidence_facts_v1"}
    select, parse, guard_review = select_evidence, parse_review, review_guard
    if review_format in {"evidence_v2", "evidence_facts_v1"}:
        from .review_context import select_evidence as select
        from .review_contract import review_guard as guard_review

        if review_format == "evidence_facts_v1":
            from .review_facts import parse_facts as parse
        else:
            from .review_contract import parse_review as parse
    options = {"profile": profile} if profile != "persistence" else {}
    if organized:
        frames = (select(context.history, control.differences, before, after, nominated_steps)
                  if review_format in {"evidence_v2", "evidence_facts_v1"} else
                  select(context.history, control.differences, before, after))
        steps = [frame["step"] for frame in frames]
        options["frames"] = frames
        context.result["review_evidence"] = frames
    else:
        steps = list(dict.fromkeys(step for step in [control.last_type_step, control.post_type_step, before, after] if step > 0))
    screenshots = [(step, context.directory / f"screenshots/{step:03d}.png") for step in steps]
    start = time.monotonic()
    raw = model.assess(task, screenshots, context.history, deadline - start, **options)
    context.result["model_calls"] += 1
    context.result["review_calls"] = 1
    context.result["review_images"] = len(screenshots)
    context.event({"step": after, "phase": "review", "raw_output": raw, "screenshot_steps": steps,
                   "elapsed_seconds": time.monotonic() - start})
    try:
        verdict = (parse(raw, set(steps), profile) if organized else
                   parse_action(raw, available, normalize_reload=True))
        if verdict["action"] != "finish":
            raise ValueError("review phase requires finish")
    except (ValueError, KeyError, TypeError) as error:
        context.result.update(termination_reason="review_invalid_output", observation=str(error))
        return
    guard = guard_review(verdict, profile=profile)
    if not guard and verdict["verdict"] != "blocked" and control.last_type_step == before:
        guard = "last_edit_not_submitted"
    context.result.update(review_model_verdict=verdict["verdict"], observation=verdict["observation"],
                          evidence_steps=verdict["evidence_steps"])
    if organized:
        context.result["field_readings"] = verdict["field_readings"]
        if review_format in {"evidence_v2", "evidence_facts_v1"}:
            context.result.update({key: verdict[key] for key in
                                  ("task_kind", "submission", "result_observed", "result_steps", "expected_rejection")})
        else:
            context.result["submission_step"] = verdict["submission_step"]
    if guard:
        context.result.update(agent_verdict="blocked", review_guard=guard,
                              termination_reason="controlled_review_guarded")
        return
    context.result.update(agent_verdict=verdict["verdict"], termination_reason="controlled_review")


class CaseAdapter(NamedTuple):
    instance: type
    login: Callable
    observer: type
    reference: Callable
    scenario_observer: type | None = None
    reviewer: Callable | None = None
    oracle: Callable | None = None
    review_profile: str = "persistence"


ADAPTERS = {
    "sweeper-041": CaseAdapter(EmlogInstance, login, ProfileObserver, reference),
    "sweeper-040": CaseAdapter(AppointmentsInstance, appointments_login, ProviderObserver, appointments_reference),
    "sweeper-005": CaseAdapter(AkauntingInstance, akaunting_login, ItemObserver, item_reference),
    "sweeper-007": CaseAdapter(AmpacheInstance, ampache_login, PlaylistObserver, playlist_reference),
    "sweeper-008": CaseAdapter(AnchorInstance, anchor_login, VariableObserver, variable_reference),
    "sweeper-150": CaseAdapter(PluxmlInstance, pluxml_login, PluxmlObserver, pluxml_reference),
    "sweeper-034": CaseAdapter(DokuwikiInstance, dokuwiki_login, DokuwikiObserver, dokuwiki_reference, scenario_observer=SupplementaryObserver),
    "sweeper-053": CaseAdapter(FlatpressInstance, flatpress_login, FlatpressObserver, flatpress_reference, scenario_observer=SupplementaryObserver),
    "sweeper-196": CaseAdapter(WondercmsInstance, wondercms_login, WondercmsObserver, wondercms_reference),
    "sweeper-185": CaseAdapter(TypesetterInstance, typesetter_login, TypesetterObserver, typesetter_reference),
}

APP_MODULES = {
    "sweeper-105": ".apps.litecart_wolf",
    "sweeper-195": ".apps.litecart_wolf",
    "sweeper-084": ".apps.impress_opencart",
    "sweeper-125": ".apps.impress_opencart",
    "sweeper-107": ".apps.mantis_phpipam",
    "sweeper-142": ".apps.mantis_phpipam",
}
