"""Native fixtures and UI-only reference flows for ImpressCMS and OpenCart."""
import json
import re
import subprocess
import time
from dataclasses import asdict
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

from guiqa.browser import settle
from guiqa.native import NativeInstance
from guiqa.oracle import Evidence, in_view, judge, request_fields
from guiqa.sft_data import observed_review, replay_flow
from guiqa.sft_flows import SftFlow, click_at, click_locator, press, scroll_to, type_text


class ImpresscmsInstance(NativeInstance):
    case_id = "sweeper-084"
    slug = "impresscms"
    web_root = "htdocs"
    health_path = "/user.php"
    php_name = "php74/php"
    runtime_description = "PHP 7.4.3 / MariaDB 10.11.14; upstream PDO installer"

    def configure(self):
        web = self.app / "htdocs"
        trust = self.socket_dir / "trust"
        for relative in ("cache/htmlpurifier/HTML", "cache/htmlpurifier/CSS", "cache/htmlpurifier/URI", "cache/htmlpurifier/Test"):
            (trust / relative).mkdir(parents=True, exist_ok=True)
        ini = self.socket_dir / "ini"
        ini.mkdir()
        (ini / "runtime.ini").write_text(
            f"pdo_mysql.default_socket={self.socket}\noutput_buffering=4096\nextension=iconv\nextension=mysqli\n"
            f"extension={self.runtime}/impress-php74-extensions/root/usr/lib/php/20190902/gd.so\n")
        self.env["PHP_INI_SCAN_DIR"] = str(ini)
        config = (web / "install/templates/mainfile.dist.php").read_text()
        values = {"ROOT_PATH": str(web), "TRUST_PATH": str(trust), "URL": self.url,
                  "DB_TYPE": "pdo.mysql", "DB_CHARSET": "utf8", "DB_PREFIX": "icms",
                  "DB_HOST": "localhost", "DB_USER": "root", "DB_PASS": "", "DB_NAME": "guiqa",
                  "DB_SALT": "guiqa-impress-salt"}
        for key, value in values.items():
            config = re.sub(rf"define\(['\"]XOOPS_{key}['\"],\s*['\"].*?['\"]\)",
                            f"define('XOOPS_{key}', {json.dumps(value)})", config)
        (web / "mainfile.php").write_text(config)
        program = """<?php
$_SERVER['REQUEST_METHOD']='POST'; $_SERVER['HTTP_HOST']='127.0.0.1';
$_SERVER['SCRIPT_FILENAME']=getcwd().'/page_tablesfill.php';
$xoopsOption['nocommon']=true;
require '../mainfile.php';
require 'common.inc.php';
require 'class/dbmanager.php';
$dbm=new db_manager();
$dbm->queryFromFile('sql/mysql.structure.sql');
$dbm->queryFromFile('sql/mysql.data.sql');
$dbm->queryFromFile('language/english/mysql.lang.data.sql');
require 'language/english/install2.php';
require 'makedata.php';
$groups=make_groups($dbm); $cm='dummy';
make_data($dbm,$cm,'Remy Holt','remyHolt','slateCup7!','admin@guiqa.test','english',$groups);
echo $dbm->report();
"""
        seed = self.socket_dir / "impress-seed.php"
        seed.write_text(program)
        self._command([str(self.runtime / self.php_name), str(seed)], "app-install.log", cwd=web / "install")
        self.sql("USE guiqa; UPDATE icms_config SET conf_value='0' WHERE conf_name IN ('debug_mode','closesite');")

    def _seed(self):
        pass

    def seed_training_page(self, fixture):
        for key, value in fixture.get("initial", {}).items():
            escaped = value.replace("'", "''")
            self.sql(f"USE guiqa; UPDATE icms_config SET conf_value='{escaped}' WHERE conf_name='{key}';")


class OpencartInstance(NativeInstance):
    case_id = "sweeper-125"
    slug = "opencart"
    web_root = "upload"
    health_path = "/admin/index.php"

    def configure(self):
        web = self.app / "upload"
        for relative in ("config.php", "admin/config.php"):
            (web / relative).touch()
        args = [str(self.runtime / "php"), "-d", f"mysqli.default_socket={self.socket}",
                "install/cli_install.php", "install", "--username", "lina.croft", "--password", "amberQuilt7!",
                "--email", "admin@guiqa.test", "--http_server", self.url + "/", "--language", "en-gb",
                "--db_driver", "mysqli", "--db_hostname", "localhost", "--db_username", "root",
                "--db_password", "", "--db_database", "guiqa", "--db_port", "3306", "--db_prefix", "oc_"]
        self._command(args, "app-install.log", cwd=web)
        if "DB_DATABASE" not in (web / "config.php").read_text():
            raise RuntimeError("OpenCart installer did not create its database configuration")

    def _seed(self):
        pass

    def seed_training_page(self, fixture):
        label = fixture["option_label"]
        self.sql("USE guiqa; UPDATE oc_product_option_value p JOIN oc_option_value_description d "
                 "ON p.option_value_id=d.option_value_id SET p.weight=" + fixture.get("initial_weight", "20")
                 + ",p.quantity=" + str(fixture.get("initial_quantity", 100))
                 + f" WHERE p.product_id=42 AND d.language_id=1 AND d.name='{label}';")


def impress_login(page, url, fixture):
    page.goto(url + "/user.php", wait_until="domcontentloaded")
    page.locator("input[name=uname]").first.fill(fixture["username"])
    page.locator("input[name=pass]").first.fill(fixture["password"])
    page.locator("input[type=submit]").first.click()
    settle(page)
    path = "/modules/system/admin.php?fct=preferences"
    if fixture.get("start", "editor") == "editor":
        path += "&op=show&confcat_id=1"
    page.goto(url + path, wait_until="domcontentloaded")
    page.locator("input[name=sitename]" if fixture.get("start", "editor") == "editor"
                 else 'a[title="Edit General Settings"]').wait_for()
    settle(page)


def opencart_login(page, url, fixture):
    page.goto(url + "/admin/", wait_until="domcontentloaded")
    page.locator("input[name=username]").fill(fixture["username"])
    page.locator("input[name=password]").fill(fixture["password"])
    page.locator("button[type=submit]").click()
    page.wait_for_url(lambda value: "user_token=" in str(value), wait_until="domcontentloaded")
    token = parse_qs(urlparse(page.url).query)["user_token"][0]
    page.goto(url + "/admin/index.php?route=catalog/product.form&user_token=" + token + "&product_id=42",
              wait_until="domcontentloaded")
    page.locator('a[href="#tab-option"]').wait_for()
    if fixture.get("start", "options") == "options":
        page.locator('a[href="#tab-option"]').click()
    settle(page)


def impress_flow(fixture, build):
    if fixture.get("read_only"):
        return SftFlow([scroll_to("input[name=sitename]")], 0, 0)
    prefix, review_steps, navigation = [], [0], []
    if fixture.get("start", "editor") == "list":
        prefix.append(click_at('a[title="Edit General Settings"]'))
        navigation.append(len(prefix))
    for field in fixture["field_order"]:
        selector = f"input[name={field}]"
        prefix.extend([scroll_to(selector), click_at(selector), press("Control+a"), type_text(fixture["values"][field])])
        review_steps.append(len(prefix))
    input_step = len(prefix)
    prefix.extend([scroll_to("#button"), click_at("#button")])
    submit_step = len(prefix)
    navigation.append(submit_step)
    pages = []
    first = prefix[0]

    def remember(page):
        pages[:] = [page]
        return first(page)

    prefix[0] = remember

    def steps():
        yield from prefix
        # The baseline save can end on the business-error page with no editor link.
        if "getConfig() on null" in pages[0].locator("body").inner_text():
            return
        yield click_at('a[title="Edit General Settings"]')
        for field in fixture["values"]:
            yield scroll_to(f"input[name={field}]")

    navigation.append(submit_step + 1)
    return SftFlow(steps(), input_step, submit_step, tuple(review_steps), tuple(navigation))


def option_row(page, fixture):
    return page.locator("#tab-option tr[id^='option-value-row-']").filter(has_text=fixture["option_label"])


def opencart_flow(fixture, build):
    steps, review_steps = [], [0]
    if fixture.get("start", "options") == "product":
        steps.append(click_at('a[href="#tab-option"]'))
    steps.extend([scroll_to(f'tr[id^="option-value-row-"]:has-text("{fixture["option_label"]}")'),
                  click_locator(lambda page: option_row(page, fixture).locator('button[title="Edit"]'))])
    for field in fixture["field_order"]:
        steps.extend([click_at(f"#input-modal-{field}"), press("Control+a"), type_text(str(fixture["values"][field]))])
        review_steps.append(len(steps))
    input_step = len(steps)
    steps.append(click_at("#modal-option #button-save"))
    # Bootstrap keeps the document scroll locked during the modal's closing transition.
    steps.append(lambda page: {"action": "wait", "seconds": 0.4})
    steps.extend([scroll_to('button[form="form-product"]'), click_at('button[form="form-product"]')])
    submit_step = len(steps)
    steps.extend([press("Control+r"), scroll_to('a[href="#tab-option"]'), click_at('a[href="#tab-option"]'),
                  scroll_to(f'tr[id^="option-value-row-"]:has-text("{fixture["option_label"]}")')])
    return SftFlow(steps, input_step, submit_step, tuple(review_steps))


class ImpressObserver:
    def __init__(self, page, fixture):
        self.page, self.fixture = page, fixture
        self.evidence = Evidence(expected=fixture["expected"])
        self.step = 0
        self.submitted_at = None
        self.reopened_at = None
        self.business_error = False
        self.visible_fields = {}
        page.on("response", self._response)

    def capture(self, step, screenshot):
        self.step = step
        if self.business_error:
            body = self.page.locator("body").inner_text()
            if "getConfig() on null" in body:
                self.evidence.events.append({"kind": "business_error_ui", "step": step,
                    "screenshot": screenshot, "error": body})
            return
        query = parse_qs(urlparse(self.page.url).query)
        if query.get("confcat_id") != ["1"] or not self.page.locator("input[name=sitename]").count():
            return
        if self.fixture.get("read_only"):
            self.evidence.preconditions = in_view(self.page.locator("input[name=sitename]"))
            if self.evidence.preconditions:
                self.evidence.actual = "general_settings_visible"
            return
        if self.submitted_at is None:
            if not self.evidence.preconditions:
                self.evidence.preconditions = all(self.page.locator(f"input[name={field}]").input_value() != value
                    for field, value in self.fixture["values"].items())
            return
        if self.reopened_at is None or self.reopened_at <= self.submitted_at or self.evidence.reopened:
            return
        for field in self.fixture["values"]:
            locator = self.page.locator(f"input[name={field}]")
            if in_view(locator):
                self.visible_fields[field] = locator.input_value()
                self.evidence.events.append({"kind": "reopened_field", "field": field, "step": step,
                    "value": self.visible_fields[field], "screenshot": screenshot})
        if self.visible_fields.keys() == self.fixture["values"].keys():
            self.evidence.reopened = self.evidence.same_object = True
            self.evidence.actual = json.dumps(self.visible_fields, ensure_ascii=False, sort_keys=True)

    def _response(self, response):
        request = response.request
        url = urlparse(response.url)
        if not url.path.endswith("/modules/system/admin.php"):
            return
        now = time.monotonic()
        if request.method == "GET" and request.is_navigation_request() and self.submitted_at is not None:
            self.reopened_at = now
        if request.method != "POST":
            return
        fields = request_fields(request)
        if fields.get("op") != "save" or parse_qs(url.query).get("fct") != ["preferences"]:
            return
        correct = all(fields.get(field) == value for field, value in self.fixture["values"].items())
        self.submitted_at = now
        self.evidence.submitted = correct
        self.business_error = correct and response.status == 200 and "getConfig() on null" in response.text()
        self.evidence.response_valid = response.status in {200, 302} and not self.business_error
        self.evidence.events.append({"kind": "submit_response", "step": self.step + 1,
            "correct_input": correct, "values": {field: fields.get(field) for field in self.fixture["values"]},
            "accepted": self.evidence.response_valid, "business_error": self.business_error, "status": response.status})

    def data(self):
        return asdict(self.evidence)


def impress_oracle(observer):
    if not observer.evidence.healthy:
        return "infra_error", "environment_failure"
    if observer.fixture.get("read_only"):
        return ("pass", "general_settings_form_visible") if observer.evidence.preconditions else ("inconclusive", "form_not_observed")
    if (observer.evidence.preconditions and observer.evidence.submitted and observer.business_error
            and any(event["kind"] == "business_error_ui" for event in observer.evidence.events)):
        return "fail", "valid_preferences_save_hit_config_handler_error"
    return judge(observer.evidence)


def impress_review(context):
    observer = context.observer
    verdict, reason = impress_oracle(observer)
    evidence = observer.evidence
    typed_steps = [entry["step"] for entry in context.history
                   if entry["action"] and entry["action"].get("action") == "type"]
    return {"action": "finish", "verdict": verdict if verdict in {"pass", "fail"} else "blocked",
            "requested_value_entered": evidence.submitted, "submission_succeeded": evidence.response_valid,
            "final_record_visible": evidence.reopened,
            "submission_observed": evidence.submitted and any(event["kind"] == "submit_response" for event in evidence.events),
            "business_rejection_visible": any(event["kind"] == "business_error_ui" for event in evidence.events),
            "expected_rejection": False,
            "observation": ("The valid General Settings submission displayed getConfig() on null. "
                            if observer.business_error else f"Reopened General Settings values: {evidence.actual}. ") + reason,
            "evidence_steps": sorted(set(typed_steps + [context.result["actions"]]))}


class OpencartObserver:
    def __init__(self, page, fixture):
        self.page, self.fixture = page, fixture
        self.evidence = Evidence(expected=fixture["expected"])
        self.step = 0
        self.submitted_at = None
        self.reopened_at = None
        self.field_names = {}
        page.on("response", self._response)

    def capture(self, step, screenshot):
        self.step = step
        query = parse_qs(urlparse(self.page.url).query)
        if query.get("route") != ["catalog/product.form"] or query.get("product_id") != ["42"]:
            return
        row = option_row(self.page, self.fixture)
        if not row.count():
            return
        for field in self.fixture["values"]:
            self.field_names[field] = row.locator(f'input[name$="[{field}]"]').get_attribute("name")
        if self.submitted_at is None:
            self.evidence.preconditions = True
            return
        if self.reopened_at is None or self.reopened_at <= self.submitted_at or self.evidence.reopened or not in_view(row):
            return
        values = {"weight": str(Decimal(row.locator("td").nth(5).inner_text().strip().lstrip("+")).normalize())}
        if "quantity" in self.fixture["values"]:
            values["quantity"] = int(row.locator("td").nth(1).inner_text().strip())
        self.evidence.reopened = self.evidence.same_object = True
        self.evidence.actual = json.dumps(values, ensure_ascii=False, sort_keys=True)
        self.evidence.events.append({"kind": "reopened_option", "step": step, "product_id": 42,
            "option_label": self.fixture["option_label"], "actual": values, "screenshot": screenshot})

    def _response(self, response):
        request = response.request
        query = parse_qs(urlparse(response.url).query)
        now = time.monotonic()
        if request.method == "GET" and request.is_navigation_request() and query.get("route") == ["catalog/product.form"] and self.submitted_at is not None:
            self.reopened_at = now
        if request.method != "POST" or query.get("route") != ["catalog/product.save"]:
            return
        fields = request_fields(request)
        values = {field: fields.get(name) for field, name in self.field_names.items()}
        correct = all(Decimal(str(values[field])) == Decimal(str(value)) for field, value in self.fixture["values"].items())
        body = response.json()
        self.submitted_at = now
        self.evidence.submitted = correct
        self.evidence.response_valid = response.status == 200 and bool(body.get("success"))
        self.evidence.events.append({"kind": "submit_response", "step": self.step + 1,
            "product_id": 42, "option_label": self.fixture["option_label"], "submitted_values": values,
            "accepted": self.evidence.response_valid, "status": response.status})

    def data(self):
        return asdict(self.evidence)


def reference(context, fixture):
    flow = impress_flow(fixture, "") if context.result["case_id"] == "sweeper-084" else opencart_flow(fixture, "")
    replay_flow(context, flow)
    review = impress_review(context) if context.result["case_id"] == "sweeper-084" else observed_review(context)
    context.result.update(agent_verdict=review["verdict"], observation=review["observation"],
                          evidence_steps=review["evidence_steps"], termination_reason="script_completed")


def register():
    from guiqa.runner import ADAPTERS, CaseAdapter
    from guiqa.sft_flows import FLOWS
    ADAPTERS["sweeper-084"] = CaseAdapter(ImpresscmsInstance, impress_login, ImpressObserver, reference,
        scenario_observer=ImpressObserver, reviewer=impress_review, oracle=impress_oracle,
        review_profile="business_outcome_v1")
    ADAPTERS["sweeper-125"] = CaseAdapter(OpencartInstance, opencart_login, OpencartObserver, reference,
        scenario_observer=OpencartObserver)
    FLOWS.update({"sweeper-084": impress_flow, "sweeper-125": opencart_flow})
