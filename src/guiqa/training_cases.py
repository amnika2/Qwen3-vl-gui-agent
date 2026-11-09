import time
from dataclasses import asdict
from urllib.parse import parse_qs, urlparse

from .browser import settle
from .oracle import Evidence, in_view, request_fields
from .sft_data import observed_review, replay_flow
from .sft_flows import dokuwiki_text, flatpress_text, pluxml_users, typesetter_text, wondercms_menu


def pluxml_login(page, url, fixture):
    page.goto(url + "/core/admin/auth.php", wait_until="domcontentloaded")
    page.locator("input[name=login]").fill(fixture["username"])
    page.locator("input[name=password]").fill(fixture["password"])
    page.locator("input[type=submit]").click()
    page.wait_for_url(lambda value: urlparse(str(value)).path != "/core/admin/auth.php", wait_until="domcontentloaded")
    page.goto(url + "/core/admin/parametres_users.php", wait_until="domcontentloaded")
    page.locator("#form_users").wait_for()
    settle(page)


class PluxmlObserver:
    def __init__(self, page, fixture):
        self.page, self.fixture = page, fixture
        self.evidence = Evidence(expected=fixture["expected"], object_id=fixture["object_id"])
        self.step = 0
        self.auth_attempted = False
        page.on("response", self._response)

    def capture(self, step, screenshot):
        self.step = step
        fixture, evidence = self.fixture, self.evidence
        if step == 0:
            evidence.preconditions = (self.page.locator("input[value=ElenaVoss]").count() == 1
                and self.page.locator("input[value=jonah19]").count() == 1
                and self.page.locator(fixture["selector"]).input_value() != fixture["input"])
        if not self.auth_attempted or evidence.reopened:
            return
        logged_in = self.page.locator("a.logout").count() == 1
        rejected = self.page.get_by_text("Incorrect login or password", exact=False).count() > 0
        if fixture["kind"] == "password" and (logged_in or rejected):
            evidence.actual = "login=" + fixture["test_username"] if logged_in else "incorrect_login"
        elif rejected:
            evidence.actual = "incorrect_login"
        elif logged_in and urlparse(self.page.url).path == "/core/admin/parametres_users.php":
            field = self.page.locator(fixture["selector"])
            if not field.count() or not in_view(field):
                return
            evidence.actual = field.input_value()
        else:
            return
        evidence.reopened = evidence.same_object = True
        evidence.events.append({"kind": "reopened_user", "step": step, "screenshot": screenshot,
                                "object_id": fixture["object_id"], "actual": evidence.actual})

    def _response(self, response):
        request, path = response.request, urlparse(response.url).path
        if request.method != "POST":
            return
        fields = request_fields(request)
        if path == "/core/admin/parametres_users.php":
            field = "users[002][password]" if self.fixture["kind"] == "password" else "users[001][name]"
            self.evidence.submitted = fields.get(field) == self.fixture["input"]
            self.evidence.response_valid = response.status == 302
            self.evidence.events.append({"kind": "submit_response", "step": self.step + 1,
                "submitted_value": fields.get(field), "accepted": response.status == 302, "status": response.status})
        elif path == "/core/admin/auth.php" and self.evidence.submitted:
            self.auth_attempted = fields.get("login") == self.fixture["test_username"] and fields.get("password") == self.fixture["test_password"]

    def data(self):
        return asdict(self.evidence)


def pluxml_reference(context, fixture):
    replay_flow(context, pluxml_users(fixture, ""))
    review = observed_review(context)
    context.result.update(agent_verdict=review["verdict"], observation=review["observation"],
                          evidence_steps=review["evidence_steps"], termination_reason="script_completed")


def dokuwiki_login(page, url, fixture):
    page.goto(url + "/doku.php?do=login", wait_until="domcontentloaded")
    page.locator("input[name=u]").fill(fixture["username"])
    page.locator("input[name=p]").fill(fixture["password"])
    page.locator("#dw__login button[type=submit]").click()
    page.wait_for_url(lambda value: "do=login" not in str(value), wait_until="domcontentloaded")
    start = fixture.get("start", "editor")
    page.goto(url + f"/doku.php?id={fixture['page_id']}" + ("&do=edit" if start == "editor" else ""), wait_until="domcontentloaded")
    page.locator("textarea[name=wikitext]" if start == "editor" else "div.page").wait_for()
    settle(page)


class DokuwikiObserver:
    def __init__(self, page, fixture):
        self.page, self.fixture = page, fixture
        self.evidence = Evidence(expected=fixture["expected"])
        self.step = 0
        self.submitted_at = None
        self.reopened_at = None
        page.on("response", self._response)

    def capture(self, step, screenshot):
        self.step = step
        page = self.page
        url = urlparse(page.url)
        if url.path != "/doku.php":
            return
        query = parse_qs(url.query)
        if query.get("do") == ["edit"]:
            textarea = page.locator(self.fixture["selector"])
            if step == 0 and textarea.count():
                self.evidence.preconditions = textarea.input_value() != self.fixture["input"]
            return
        if self.submitted_at is None or self.evidence.reopened:
            return
        if self.reopened_at is None or self.reopened_at <= self.submitted_at:
            return
        content = page.locator("div.page")
        if not content.count():
            return
        if self.fixture["expected"] in content.inner_text():
            self.evidence.reopened = True
            self.evidence.same_object = True
            self.evidence.actual = self.fixture["expected"]
            self.evidence.events.append({"kind": "reopened_page", "step": step,
                "object_id": self.fixture["page_id"], "actual": self.evidence.actual, "screenshot": screenshot})

    def data(self):
        return asdict(self.evidence)

    def _response(self, response):
        request = response.request
        url = urlparse(response.url)
        now = time.monotonic()
        if url.path != "/doku.php":
            return
        if request.method == "GET" and request.is_navigation_request() and self.submitted_at is not None:
            self.reopened_at = now
        if request.method != "POST":
            return
        fields = request_fields(request)
        if "do[save]" not in fields:
            return
        self.submitted_at = now
        self.reopened_at = None
        self.evidence.submitted = fields.get("wikitext") == self.fixture["input"]
        self.evidence.reopened = False
        self.evidence.actual = None
        self.evidence.same_object = False
        self.evidence.response_valid = response.status == 302
        self.evidence.events.append({"kind": "submit_response", "step": self.step + 1,
            "object_id": self.fixture["page_id"], "submitted_value": fields.get("wikitext"),
            "status": response.status, "accepted": self.evidence.response_valid, "elapsed_marker": now})


def dokuwiki_reference(context, fixture):
    replay_flow(context, dokuwiki_text(fixture, ""))
    review = observed_review(context)
    context.result.update(agent_verdict=review["verdict"], observation=review["observation"],
                          evidence_steps=review["evidence_steps"], termination_reason="script_completed")


def flatpress_login(page, url, fixture):
    page.goto(url + "/login.php", wait_until="domcontentloaded")
    page.locator("input[name=user]").fill(fixture["username"])
    page.locator("input[name=pass]").fill(fixture["password"])
    page.locator("input[name=submit]").click()
    page.wait_for_url(lambda value: "login.php" not in str(value), wait_until="domcontentloaded")
    start = fixture.get("start", "editor")
    page.goto(url + "/admin.php?p=static" + (f"&action=write&page={fixture['page_id']}" if start == "editor" else ""), wait_until="domcontentloaded")
    page.locator("textarea[name=content]" if start == "editor" else ".entrylist").wait_for()
    settle(page)


class FlatpressObserver:
    def __init__(self, page, fixture):
        self.page, self.fixture = page, fixture
        self.evidence = Evidence(expected=fixture["expected"])
        self.step = 0
        self.submitted_at = None
        self.reopened_at = None
        page.on("response", self._response)

    def capture(self, step, screenshot):
        self.step = step
        page = self.page
        url = urlparse(page.url)
        if url.path != "/admin.php":
            return
        query = parse_qs(url.query)
        if query.get("p") != ["static"] or query.get("action") != ["write"]:
            return
        textarea = page.locator(self.fixture["selector"])
        if not textarea.count():
            return
        value = textarea.input_value()
        if step == 0:
            self.evidence.preconditions = value != self.fixture["input"]
            return
        if self.submitted_at is None or self.evidence.reopened:
            return
        if self.reopened_at is None or self.reopened_at <= self.submitted_at:
            return
        if value == self.fixture["input"]:
            self.evidence.reopened = True
            self.evidence.same_object = True
            self.evidence.actual = value
            self.evidence.events.append({"kind": "reopened_editor", "step": step,
                "object_id": self.fixture["page_id"], "actual": value, "screenshot": screenshot})

    def data(self):
        return asdict(self.evidence)

    def _response(self, response):
        request = response.request
        url = urlparse(response.url)
        now = time.monotonic()
        if url.path != "/admin.php":
            return
        if request.method == "GET" and request.is_navigation_request() and self.submitted_at is not None \
                and parse_qs(url.query).get("action") == ["write"]:
            self.reopened_at = now
        if request.method != "POST" or parse_qs(url.query).get("action") != ["write"]:
            return
        fields = request_fields(request)
        if "save" not in fields:
            return
        self.submitted_at = now
        self.reopened_at = None
        self.evidence.submitted = fields.get("content") == self.fixture["input"]
        self.evidence.reopened = False
        self.evidence.actual = None
        self.evidence.same_object = False
        self.evidence.response_valid = response.status == 302
        self.evidence.events.append({"kind": "submit_response", "step": self.step + 1,
            "object_id": self.fixture["page_id"], "submitted_value": fields.get("content"),
            "status": response.status, "accepted": self.evidence.response_valid, "elapsed_marker": now})


def flatpress_reference(context, fixture):
    replay_flow(context, flatpress_text(fixture, ""))
    review = observed_review(context)
    context.result.update(agent_verdict=review["verdict"], observation=review["observation"],
                          evidence_steps=review["evidence_steps"], termination_reason="script_completed")


def wondercms_login(page, url, fixture):
    page.goto(url + "/loginURL", wait_until="domcontentloaded")
    page.locator("input[name=password]").fill(fixture["password"])
    page.locator("button[type=submit]").click()
    page.wait_for_url(lambda value: "loginURL" not in str(value), wait_until="domcontentloaded")
    page.locator(".settings.button").wait_for()
    settle(page)


class WondercmsObserver:
    def __init__(self, page, fixture):
        self.page, self.fixture = page, fixture
        self.evidence = Evidence(expected=fixture["expected"])
        self.step = 0
        self.submitted_at = None
        self.reopened_at = None
        page.on("response", self._response)

    def capture(self, step, screenshot):
        self.step = step
        fixture, evidence = self.fixture, self.evidence
        if step == 0:
            evidence.preconditions = self.page.locator(".settings.button").count() == 1
            return
        if self.submitted_at is None or evidence.reopened:
            return
        if self.reopened_at is None or self.reopened_at <= self.submitted_at:
            return
        first = self.page.locator("#menuSettings div.editText[data-target='menuItemUpdate']").first
        if not first.count() or not in_view(first):
            return
        # Record whatever order is actually observed; judge() compares it to expected,
        # so a defect build (order unchanged) is reported as fail, not inconclusive.
        name = first.inner_text().strip()
        evidence.reopened = True
        evidence.same_object = True
        evidence.actual = name
        evidence.events.append({"kind": "reopened_menu", "step": step, "screenshot": screenshot,
                                "object_id": fixture["menu"], "actual": name})

    def data(self):
        return asdict(self.evidence)

    def _response(self, response):
        request = response.request
        now = time.monotonic()
        if request.method == "GET" and request.is_navigation_request() and self.submitted_at is not None:
            self.reopened_at = now
        if request.method != "POST":
            return
        fields = request_fields(request)
        if fields.get("target") != "menuItemOrder":
            return
        direction = "1" if self.fixture["direction"] == "down" else "-1"
        self.submitted_at = now
        self.reopened_at = None
        self.evidence.submitted = fields.get("content") == direction and fields.get("menu") == self.fixture["menu"]
        self.evidence.reopened = False
        self.evidence.actual = None
        self.evidence.same_object = False
        self.evidence.response_valid = response.status == 200
        self.evidence.events.append({"kind": "submit_response", "step": self.step + 1,
            "object_id": self.fixture["menu"], "submitted_value": fields.get("content"),
            "status": response.status, "accepted": self.evidence.response_valid, "elapsed_marker": now})


def wondercms_reference(context, fixture):
    replay_flow(context, wondercms_menu(fixture, ""))
    review = observed_review(context)
    context.result.update(agent_verdict=review["verdict"], observation=review["observation"],
                          evidence_steps=review["evidence_steps"], termination_reason="script_completed")


def typesetter_login(page, url, fixture):
    page.goto(url + "/index.php/Admin", wait_until="domcontentloaded")
    page.locator("input[name=username]").fill(fixture["username"])
    page.locator("input[name=password]").fill(fixture["password"])
    page.locator("input[type=submit]").click()
    page.wait_for_url(lambda value: urlparse(str(value)).path == "/index.php/Admin", wait_until="domcontentloaded")
    page.locator("#admin_breadcrumbs").wait_for()
    page.goto(url + "/", wait_until="domcontentloaded")
    page.locator(fixture["edit"]).wait_for()
    settle(page)


class TypesetterObserver:
    def __init__(self, page, fixture):
        self.page, self.fixture = page, fixture
        self.evidence = Evidence(expected=fixture["expected"])
        self.step = 0
        self.submitted_at = None
        self.reopened_at = None
        self.first_submitted_at = None
        self.initial_matches = False
        page.on("response", self._response)

    def capture(self, step, screenshot):
        self.step = step
        path = urlparse(self.page.url).path
        if path.rstrip("/") not in ("", "/index.php"):
            return
        area = self.page.locator(self.fixture["area"])
        if not area.count() or not in_view(area):
            return
        value = area.inner_text().strip()
        if step == 0:
            self.initial_matches = value == self.fixture["key"]
            self.evidence.preconditions = self.initial_matches and "first" not in self.fixture
            return
        if self.first_submitted_at is not None and self.submitted_at is None:
            if (self.reopened_at is not None and self.reopened_at > self.first_submitted_at
                    and self.initial_matches and value == self.fixture["first"]):
                self.evidence.preconditions = True
                self.evidence.events.append({"kind": "first_reopened_area", "step": step,
                    "object_id": self.fixture["key"], "actual": value, "screenshot": screenshot})
            return
        if self.submitted_at is None or self.evidence.reopened:
            return
        if self.reopened_at is None or self.reopened_at <= self.submitted_at:
            return
        self.evidence.reopened = True
        self.evidence.same_object = True
        self.evidence.actual = value
        self.evidence.events.append({"kind": "reopened_area", "step": step,
            "object_id": self.fixture["key"], "actual": value, "screenshot": screenshot})

    def data(self):
        return asdict(self.evidence)

    def _response(self, response):
        request = response.request
        path = urlparse(response.url).path
        now = time.monotonic()
        if (request.method == "GET" and request.is_navigation_request()
                and (self.submitted_at is not None or self.first_submitted_at is not None)):
            self.reopened_at = now
        if request.method != "POST" or path != "/index.php/Admin_Theme_Content/Text":
            return
        fields = request_fields(request)
        if fields.get("cmd") != "savetext":
            return
        value = fields.get("value")
        if value != self.fixture["input"]:
            if value == self.fixture.get("first") and response.status == 200:
                self.first_submitted_at = now
                self.reopened_at = None
                self.evidence.preconditions = False
                self.evidence.events.append({"kind": "first_submit", "step": self.step + 1,
                    "value": value, "key": fields.get("key"), "elapsed_marker": now})
            return
        self.submitted_at = now
        self.reopened_at = None
        self.evidence.submitted = True
        self.evidence.reopened = False
        self.evidence.actual = None
        self.evidence.same_object = False
        self.evidence.response_valid = response.status == 200
        self.evidence.events.append({"kind": "submit_response", "step": self.step + 1,
            "object_id": self.fixture["key"], "submitted_value": value, "key": fields.get("key"),
            "status": response.status, "accepted": self.evidence.response_valid, "elapsed_marker": now})


def typesetter_reference(context, fixture):
    replay_flow(context, typesetter_text(fixture, ""))
    review = observed_review(context)
    context.result.update(agent_verdict=review["verdict"], observation=review["observation"],
                          evidence_steps=review["evidence_steps"], termination_reason="script_completed")
