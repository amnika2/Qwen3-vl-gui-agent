"""Native fixtures and screenshot-action collectors for MantisBT and phpIPAM."""
import hashlib
import json
import re
import shutil
import subprocess
import time
from dataclasses import asdict
from urllib.parse import parse_qs, urlparse

from guiqa.browser import settle
from guiqa.native import NativeInstance
from guiqa.oracle import Evidence, in_view, request_fields
from guiqa.sft_flows import SftFlow, click_at, click_locator, press, scroll_to, type_text


class MantisInstance(NativeInstance):
    case_id = "sweeper-107"
    slug = "mantisbt"
    web_root = "."
    health_path = "/login_page.php"

    def configure(self):
        shutil.copytree(self.root / "vendor/apps/mantisbt/base/vendor", self.app / "vendor", dirs_exist_ok=True)
        (self.app / "config/config_inc.php").write_text(
            "<?php\n$g_hostname='localhost';$g_db_username='root';$g_db_password='';"
            "$g_database_name='guiqa';$g_db_type='mysqli';"
            "$g_crypto_master_salt='guiqa-mantis-fixture-2026';$g_default_language='english';"
            "$g_enable_email_notification=OFF;$g_allow_signup=OFF;$g_signup_use_captcha=OFF;"
            "$g_show_product_version=ON;$g_report_issues_for_unreleased_versions_threshold=DEVELOPER;"
            f"$g_path={json.dumps(self.url + '/')};\n")
        self._command([str(self.runtime / "php"), "-d", f"mysqli.default_socket={self.socket}",
                       "admin/upgrade_unattended.php"], "schema-install.log", cwd=self.app)

    def _seed(self):
        program = ("require 'core.php';require_api('user_api.php');require_api('project_api.php');"
                   "require_api('version_api.php');require_api('category_api.php');"
                   "$uid=user_create('elena.park','windChime4!','elena@example.test',REPORTER,false,true,'Elena Park');"
                   "$pid=project_create('Softfield Tracker','Synthetic issue collection project',10,VS_PUBLIC);"
                   "category_add($pid,'General');version_add($pid,'1.1.0',VERSION_RELEASED);"
                   "echo $pid;")
        self._command([str(self.runtime / "php"), "-d", f"mysqli.default_socket={self.socket}", "-r", program],
                      "fixture-seed.log", cwd=self.app)

    def seed_training_page(self, fixture):
        pass


class PhpipamInstance(NativeInstance):
    case_id = "sweeper-142"
    slug = "phpipam"
    web_root = "."
    health_path = "/login/"

    def configure(self):
        prepared = self.root / "vendor/apps/phpipam/base"
        for match in re.finditer(r"(?m)^\s*path = (.+)$", (prepared / ".gitmodules").read_text()):
            relative = match[1].strip()
            source = prepared / relative
            if source.is_dir():
                shutil.copytree(source, self.app / relative, dirs_exist_ok=True, ignore=shutil.ignore_patterns('.git'))
        ini = self.socket_dir / "php-ini"
        ini.mkdir()
        gmp = self.root / "vendor/phpipam-runtime/root/usr/lib/php/20230831/gmp.so"
        pear = self.root / "vendor/phpipam-runtime/root/usr/share/php"
        (ini / "phpipam.ini").write_text(f"pdo_mysql.default_socket={self.socket}\nextension={gmp}\ninclude_path=.:{pear}\n")
        self.env["PHP_INI_SCAN_DIR"] = str(ini)
        config = (self.app / "config.dist.php").read_text()
        for key, value in {"host":"localhost", "user":"root", "pass":"", "name":"guiqa"}.items():
            config = re.sub(rf"(?m)^\$db\['{key}'\] = .*?;", f"$db['{key}'] = {json.dumps(value)};", config)
        config = config.replace("$disable_installer = false;", "$disable_installer = true;")
        (self.app / "config.php").write_text(config)
        self.sql("USE guiqa;" + (self.app / "db/SCHEMA.sql").read_text())

    def _seed(self):
        hashed = subprocess.check_output([str(self.runtime / "php"), "-r",
            "echo crypt(stream_get_contents(STDIN), '$6$rounds=3000$guiqaCollect2026$');"],
            input="rackPin9!", text=True, env=self.env)
        self.sql("USE guiqa; UPDATE users SET username='NoahBrett',password='" + hashed
                 + "',passChange='No',real_name='Noah Brett' WHERE id=1;"
                 "UPDATE settings SET siteDomain='127.0.0.1',siteURL='/',enableDNSresolving=0;")

    def seed_training_page(self, fixture):
        values = {"description": fixture["description"], "hostname": fixture["hostname"]}
        assignments = ",".join(key + "='" + value.replace("'", "''") + "'" for key, value in values.items())
        self.sql(f"USE guiqa;UPDATE ipaddresses SET {assignments} WHERE id={fixture['object_id']};")


def mantis_login(page, url, fixture):
    page.goto(url + "/login_page.php", wait_until="domcontentloaded")
    page.locator("input[name=username]").fill(fixture["username"])
    page.locator("input[type=submit]").click()
    page.locator("input[name=password]").fill(fixture["password"])
    page.locator("input[type=submit]").click()
    page.wait_for_url(lambda value: 'login' not in urlparse(str(value)).path)
    page.goto(url + "/bug_report_page.php?project_id=1", wait_until="domcontentloaded")
    page.locator("input[name=summary]").wait_for()
    settle(page)


def phpipam_login(page, url, fixture):
    page.goto(url + "/login/", wait_until="domcontentloaded")
    page.locator("#username").fill(fixture["username"])
    page.locator("#password").fill(fixture["password"])
    page.locator("input[type=submit]").click()
    page.wait_for_url(lambda value: '/login' not in urlparse(str(value)).path)
    page.goto(url + "/index.php?page=subnets&section=1&subnetId=3", wait_until="domcontentloaded")
    page.locator(".ipaddress_subnet").wait_for()
    settle(page)


def mantis_flow(fixture, build):
    fields = fixture.get("field_order", ["summary", "description"])
    steps = [click_at("#category_id"), press("Home"), press("ArrowDown"), press("Enter")]
    if fixture["version"]:
        steps += [click_at("#product_version"), press("Home"), press("ArrowDown"), press("Enter")]
    review_steps = [0]
    for field in fields:
        steps += [scroll_to(f"[name={field}]"), click_at(f"[name={field}]"), type_text(fixture[field])]
        review_steps.append(len(steps))
    steps += [scroll_to("input[type=submit]"), click_at("input[type=submit]")]
    submit = len(steps)
    steps.append(lambda page: {"action":"press","key":"Control+r"} if urlparse(page.url).path == "/view.php"
                 else {"action":"wait","seconds":0.3})
    return SftFlow(steps, submit - 2, submit, tuple(review_steps), (submit,))


class MantisObserver:
    def __init__(self, page, fixture):
        self.page, self.fixture = page, fixture
        self.evidence = Evidence(expected=fixture["summary"])
        self.step = 0
        self.submitted_at = None
        self.reopened_at = None
        self.business_error = False
        self.response_code = None
        page.on("response", self._response)

    def capture(self, step, screenshot):
        self.step = step
        path = urlparse(self.page.url).path
        if step == 0:
            self.evidence.preconditions = (path == "/bug_report_page.php"
                and self.page.locator("#summary").count() == 1
                and self.page.locator("input[name=project_id]").input_value()==str(self.fixture["project_id"])
                and self.page.locator('#product_version option').all_text_contents() == ['\xa0', '1.1.0'])
        if not self.evidence.submitted or not self.evidence.response_valid:
            return
        text = self.page.locator("body").inner_text()
        if "APPLICATION ERROR #29" in text and re.search(r'Invalid value for ["“]?version', text):
            self.business_error = True
            self.evidence.actual = "APPLICATION ERROR #29: Invalid value for version"
            self.evidence.events.append({"kind":"business_error", "step":step, "object_id":self.fixture["summary"],
                "actual":self.evidence.actual, "screenshot":screenshot})
            return
        if path != "/view.php" or self.reopened_at is None:
            return
        summary = self.page.locator("td.bug-summary")
        description = self.page.locator("td.bug-description")
        if not summary.count() or not description.count():
            return
        self.evidence.reopened = True
        self.evidence.same_object = re.sub(r'^\d+:\s*','',summary.inner_text().strip())==self.fixture["summary"]
        extra={key:self.page.locator(selector).inner_text().strip() for key,selector in {
            "steps_to_reproduce":"td.bug-steps-to-reproduce","additional_info":"td.bug-additional-information"
            }.items() if key in self.fixture}
        self.evidence.actual = self.fixture["summary"] if (self.evidence.same_object
            and description.inner_text().strip() == self.fixture["description"]
            and all(value==self.fixture[key] for key,value in extra.items())) else text
        self.evidence.events.append({"kind":"reopened_issue", "step":step,
            "object_id":parse_qs(urlparse(self.page.url).query).get('id'),
            "summary":summary.inner_text(),"description":description.inner_text(),"extra_fields":extra,"screenshot":screenshot})

    def _response(self, response):
        request = response.request
        path = urlparse(response.url).path
        now = time.monotonic()
        if request.method == "GET" and path == "/view.php" and self.submitted_at is not None:
            self.reopened_at = now
        if request.method != "POST" or path != "/bug_report.php":
            return
        fields = request_fields(request)
        wanted = ["summary", "description"] + [key for key in ("steps_to_reproduce", "additional_info") if key in self.fixture]
        for key in wanted:
            fields[key]=fields.get(key,"").replace("\r\n","\n")
        self.evidence.submitted = (all(fields.get(key) == self.fixture[key] for key in wanted)
            and fields.get("product_version", "") == self.fixture["version"]
            and fields.get("project_id")==str(self.fixture["project_id"])
            and fields.get("category_id") not in (None, "", "0"))
        self.submitted_at = now
        self.evidence.response_valid = response.status in (200, 302, 400)
        self.response_code = response.status
        self.evidence.events.append({"kind":"submit_response","step":self.step+1,
            "submitted_fields":{key:fields.get(key) for key in wanted+["product_version","category_id"]},
            "correct_input":self.evidence.submitted,"status":response.status,"accepted":response.status==302})

    def data(self):
        return {**asdict(self.evidence), "business_error":self.business_error}


def mantis_oracle(observer):
    e = observer.evidence
    if not e.healthy:
        return "infra_error", "environment_failure"
    if not (e.preconditions and e.submitted and e.response_valid):
        return "inconclusive", "trigger_not_verified"
    if observer.business_error:
        return "fail", "released_version_rejected_after_valid_submit"
    if e.reopened and e.same_object:
        return ("pass", "created_issue_matches") if e.actual == e.expected else ("fail", "created_issue_differs")
    return "inconclusive", "issue_not_observed"


def mantis_review(context):
    observer = context.observer
    verdict, reason = mantis_oracle(observer)
    e = observer.evidence
    entered_steps=[row["step"] for row in context.history
                   if row["action"] and row["action"]["action"]=="type"]
    submitted_steps=[event["step"] for event in e.events if event["kind"]=="submit_response"]
    evidence_steps=list(dict.fromkeys(entered_steps+submitted_steps+[context.result["actions"]]))
    return {"action":"finish", "verdict":verdict if verdict in ("pass","fail") else "blocked",
            "requested_value_entered":e.submitted,"submission_succeeded":e.submitted and e.response_valid and not observer.business_error,
            "final_record_visible":e.reopened and e.same_object,
            "submission_observed":e.submitted and e.response_valid,
            "business_rejection_visible":observer.business_error,"expected_rejection":False,
            "observation":f"{reason}: {e.actual}","evidence_steps":evidence_steps}


def mantis_reference(context, fixture):
    from guiqa.sft_data import replay_flow
    replay_flow(context, mantis_flow(fixture, ""))
    review = mantis_review(context)
    context.result.update(agent_verdict=review["verdict"],observation=review["observation"],
                          evidence_steps=review["evidence_steps"],termination_reason="script_completed")


def phpipam_flow(fixture, build):
    selector = f".edit_ipaddress[data-id='{fixture['object_id']}']"
    edit = click_at(selector)
    steps, review_steps = [scroll_to(selector),edit], [0, 2]
    for field in fixture.get("field_order", list(fixture["changes"])):
        value = fixture["changes"][field]
        steps += [click_at(f"input[name={field}]"), press("Control+a")]
        if field in fixture.get("append", {}):
            steps += [press("ArrowRight"), type_text(fixture["append"][field])]
        else:
            steps.append(type_text(value) if value else press("Backspace"))
        review_steps.append(len(steps))
    input_step = len(steps)
    steps += [click_at("#editIPAddressSubmit"), scroll_to(selector), edit]
    return SftFlow(steps, input_step, input_step + 1, tuple(review_steps), (input_step + 1,))


class PhpipamObserver:
    def __init__(self, page, fixture):
        self.page,self.fixture=page,fixture
        expected={"description":fixture["description"],"hostname":fixture["hostname"],**fixture["changes"]}
        self.evidence=Evidence(expected=json.dumps(expected,sort_keys=True,ensure_ascii=False),object_id=fixture["object_id"])
        self.step=0
        self.submitted_at=None
        self.reopened_at=None
        self.opened_id=None
        page.on("response",self._response)

    def capture(self, step, screenshot):
        self.step=step
        description=self.page.locator("input[name=description]")
        hostname=self.page.locator("input[name=hostname]")
        if not description.count() or not hostname.count() or not in_view(description):
            return
        actual={"description":description.input_value(),"hostname":hostname.input_value()}
        visible_id=self.page.locator("input[name=addressId]").input_value()
        if self.submitted_at is None:
            matches=(visible_id==str(self.fixture["object_id"])
                and actual=={key:self.fixture[key] for key in ("description","hostname")})
            if matches and not self.evidence.preconditions:
                self.evidence.preconditions=True
                self.evidence.events.append({"kind":"initial_address","step":step,"object_id":visible_id,
                    "actual":actual,"screenshot":screenshot})
            return
        if self.evidence.reopened or self.reopened_at is None or self.reopened_at<=self.submitted_at:
            return
        self.evidence.reopened=True
        self.evidence.same_object=visible_id==str(self.fixture["object_id"]) and self.opened_id==self.fixture["object_id"]
        self.evidence.actual=json.dumps(actual,sort_keys=True,ensure_ascii=False)
        self.evidence.events.append({"kind":"reopened_address","step":step,"object_id":visible_id,
            "actual":actual,"screenshot":screenshot})

    def _response(self,response):
        request=response.request
        path=urlparse(response.url).path
        if request.method!="POST":
            return
        fields=request_fields(request)
        now=time.monotonic()
        if path.endswith("/address-modify.php") and fields.get("action")=="edit":
            self.opened_id=int(fields["id"])
            if self.submitted_at is not None:
                self.reopened_at=now
        if not path.endswith("/address-modify-submit.php") or fields.get("action")!="edit":
            return
        self.submitted_at=now
        self.reopened_at=None
        self.evidence.submitted=(fields.get("id")==str(self.fixture["object_id"])
            and all(fields.get(key)==value for key,value in self.fixture["changes"].items()))
        self.evidence.response_valid=response.status==200 and 'alert-success' in response.text()
        self.evidence.events.append({"kind":"submit_response","step":self.step+1,
            "object_id":fields.get("id"),"submitted_fields":{key:fields.get(key) for key in ("description","hostname")},
            "status":response.status,"accepted":self.evidence.response_valid})

    def data(self):
        return asdict(self.evidence)


def phpipam_reference(context,fixture):
    from guiqa.sft_data import replay_flow,observed_review
    replay_flow(context,phpipam_flow(fixture,""))
    review=observed_review(context)
    context.result.update(agent_verdict=review["verdict"],observation=review["observation"],
                          evidence_steps=review["evidence_steps"],termination_reason="script_completed")


def register():
    from guiqa.runner import ADAPTERS,CaseAdapter
    from guiqa.sft_flows import FLOWS
    ADAPTERS["sweeper-107"]=CaseAdapter(MantisInstance,mantis_login,MantisObserver,mantis_reference,
        reviewer=mantis_review,oracle=mantis_oracle,review_profile="business_outcome_v1")
    ADAPTERS["sweeper-142"]=CaseAdapter(PhpipamInstance,phpipam_login,PhpipamObserver,phpipam_reference)
    FLOWS["sweeper-107"]=mantis_flow
    FLOWS["sweeper-142"]=phpipam_flow
