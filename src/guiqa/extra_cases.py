import time
from dataclasses import asdict
from urllib.parse import parse_qs, urlparse

from .browser import settle
from .oracle import Evidence, in_view, request_fields


def ampache_login(page, url, fixture):
    page.goto(url + "/login.php", wait_until="networkidle")
    page.locator('input[name=username]').fill(fixture["username"])
    page.locator('input[name=password]').fill(fixture["password"])
    page.locator('#loginbutton').click()
    page.wait_for_url(lambda value: "/login.php" not in str(value), wait_until="domcontentloaded")
    path = "/browse.php?action=smartplaylist" if fixture["category"] == "target" else "/search.php?type=song"
    page.goto(url + path, wait_until="networkidle")


def anchor_login(page, url, fixture):
    page.goto(url + "/index.php/admin/login", wait_until="networkidle")
    page.locator('input[name=user]').fill(fixture["username"])
    page.locator('input[name=pass]').fill(fixture["password"])
    page.get_by_role("button", name="Login", exact=True).click()
    page.wait_for_url(lambda value: "/admin/login" not in str(value), wait_until="domcontentloaded")
    path = "/index.php/admin/extend/variables" if fixture["category"] == "target" else "/index.php/admin/extend/variables/add"
    page.goto(url + path, wait_until="networkidle")


class PlaylistObserver:
    def __init__(self, page, fixture):
        self.page, self.fixture = page, fixture
        self.evidence = Evidence(expected=fixture["expected"])
        self.step = 0
        self.submitted_at = None
        self.reloaded_at = None
        page.on("response", self._response)

    def capture(self, step, screenshot):
        self.step = step
        if self.fixture["category"] == "preservation" and step == 0:
            self.evidence.preconditions = bool(self.page.locator("#savesearchbutton").count())
        if self.fixture["category"] == "target":
            row = self.page.locator(f"tr#smartplaylist_row_{self.fixture['object_id']}")
            cell = row.locator(".cel_playlist")
            if not cell.count():
                return
            name = cell.inner_text().strip()
            if self.submitted_at is None and name == "Rename Me Smart" and in_view(row):
                self.evidence.preconditions = True
            if self.reloaded_at is not None and not self.evidence.reopened and in_view(row):
                self.evidence.actual = name
                self.evidence.object_id = self.fixture["object_id"]
                self._observe(step, screenshot)
        elif self.reloaded_at is not None and not self.evidence.reopened:
            rows = self.page.locator("tr[id^=smartplaylist_row_]")
            for row in rows.all():
                name = row.locator(".cel_playlist").inner_text().strip()
                if name.startswith(self.fixture["username"]) and in_view(row):
                    self.evidence.actual = "created"
                    self.evidence.object_id = int(row.get_attribute("id").rsplit("_", 1)[1])
                    self._observe(step, screenshot)
                    return

    def _observe(self, step, screenshot):
        self.evidence.reopened = self.evidence.same_object = True
        self.evidence.events.append({"kind": "saved_playlist", "step": step, "screenshot": screenshot,
                                     "object_id": self.evidence.object_id, "actual": self.evidence.actual})

    def _response(self, response):
        request, url = response.request, urlparse(response.url)
        if (self.submitted_at is not None and response.status == 200
                and url.path == "/browse.php" and parse_qs(url.query).get("action") == ["smartplaylist"]):
            self.reloaded_at = time.monotonic()
        if request.method != "POST":
            return
        query = {key: values[-1] for key, values in parse_qs(url.query).items()}
        fields = {**query, **request_fields(request)}
        if self.fixture["category"] == "target":
            if fields.get("type") != "search_row":
                return
            correct = fields.get("name") == self.fixture["expected"] and fields.get("id") == str(self.fixture["object_id"])
        else:
            if fields.get("action") != "save_as_smartplaylist":
                return
            correct = True
        self.submitted_at, self.reloaded_at = time.monotonic(), None
        self.evidence.submitted = correct
        self.evidence.response_valid = response.status == 200
        self.evidence.reopened = False
        self.evidence.actual = None
        self.evidence.events.append({"kind": "submit_response", "step": self.step + 1,
            "submitted_value": fields.get("name", "save_as_smartplaylist"), "status": response.status,
            "object_id": fields.get("id"), "correct": correct})

    def data(self):
        return asdict(self.evidence)


class VariableObserver:
    def __init__(self, page, fixture):
        self.page, self.fixture = page, fixture
        self.evidence = Evidence(expected=fixture["expected"], object_id=fixture["object_id"])
        self.step = 0
        self.submitted_at = None
        self.reopened_at = None
        self.base = "/index.php/admin/extend/variables"
        page.on("response", self._response)

    def capture(self, step, screenshot):
        self.step = step
        path = urlparse(self.page.url).path
        if self.fixture["category"] == "target":
            if path != self.base + "/edit/" + self.fixture["object_id"]:
                return
            name, value = self.page.locator('input[name=key]'), self.page.locator('textarea[name=value]')
            same = name.count() and name.input_value() == self.fixture["name"]
            if same and self.submitted_at is None and value.input_value() == "Hello Anchor":
                self.evidence.preconditions = True
            if same and self.reopened_at is not None and not self.evidence.reopened and in_view(value):
                self.evidence.actual = value.input_value()
                self._observe(step, screenshot)
        else:
            if step == 0:
                self.evidence.preconditions = path == self.base + "/add" and self.page.locator('input[name=key]').input_value() == ""
            if path == self.base and self.reopened_at is not None and not self.evidence.reopened:
                row = self.page.locator(f'a[href$="/edit/{self.fixture["object_id"]}"]')
                if row.count() == 1 and in_view(row):
                    text = row.inner_text()
                    self.evidence.actual = self.fixture["expected"] if self.fixture["expected"] in text else text
                    self._observe(step, screenshot)

    def _observe(self, step, screenshot):
        self.evidence.reopened = self.evidence.same_object = True
        self.evidence.events.append({"kind": "saved_variable", "step": step, "screenshot": screenshot,
                                     "object_id": self.evidence.object_id, "actual": self.evidence.actual})

    def _response(self, response):
        request, path = response.request, urlparse(response.url).path
        target = self.base + ("/edit/" + self.fixture["object_id"] if self.fixture["category"] == "target" else "")
        if request.method == "GET" and path == target and self.submitted_at is not None:
            self.reopened_at = time.monotonic()
        submit = self.base + ("/edit/" + self.fixture["object_id"] if self.fixture["category"] == "target" else "/add")
        if request.method != "POST" or path != submit:
            return
        fields = request_fields(request)
        self.submitted_at, self.reopened_at = time.monotonic(), None
        self.evidence.submitted = fields.get("key") == self.fixture["name"] and fields.get("value") == self.fixture["expected"]
        self.evidence.response_valid = response.status in (200, 302, 303)
        self.evidence.reopened = False
        self.evidence.actual = None
        self.evidence.events.append({"kind": "submit_response", "step": self.step + 1,
             "submitted_value": fields.get("value"), "object_id": self.fixture["object_id"], "status": response.status})

    def data(self):
        return asdict(self.evidence)


def playlist_reference(context, fixture):
    page = context.page
    if fixture["category"] == "target":
        row = page.locator(f"#smartplaylist_row_{fixture['object_id']}")
        row.scroll_into_view_if_needed()
        context.capture("Locate the seeded playlist")
        row.locator('a[id^=edit_playlist_]').click()
        context.result["actions"] += 1
        context.capture("Open playlist Edit dialog")
        page.locator('input[name=name]').fill(fixture["expected"])
        context.result["actions"] += 1
        context.capture("Replace playlist name")
        with page.expect_response(lambda response: response.request.method == "POST"):
            page.get_by_role("button", name="Save", exact=True).click()
        context.result["actions"] += 1
        context.capture("Submit playlist rename")
    else:
        page.locator("#savesearchbutton").click()
        context.result["actions"] += 1
        settle(page)
        context.capture("Save search as Smart Playlist")
    if fixture["category"] == "target":
        url = page.url.split("/", 3)[:3]
        page.goto("/".join(url) + "/browse.php?action=smartplaylist", wait_until="networkidle")
    else:
        with page.expect_response(lambda response: urlparse(response.url).path == "/browse.php" and response.request.method == "POST"):
            page.get_by_role("button", name="Continue", exact=True).click()
        settle(page)
    if fixture["category"] == "target":
        row = page.locator(f"#smartplaylist_row_{fixture['object_id']}")
        actual = row.locator(".cel_playlist").inner_text().strip()
    else:
        row = page.locator('tr[id^=smartplaylist_row_]').filter(has_text=fixture["username"] + " - ").first
        actual = "created" if row.count() else "missing"
    if row.count():
        row.scroll_into_view_if_needed()
    context.result["actions"] += 1
    context.capture("Reopen Smart Playlists")
    context.result.update(agent_verdict="pass" if actual == fixture["expected"] else "fail",
                          termination_reason="script_completed", observation=actual,
                          evidence_steps=[context.result["actions"]])


def variable_reference(context, fixture):
    page = context.page
    origin = "/".join(page.url.split("/", 3)[:3])
    if fixture["category"] == "target":
        page.locator(f'a[href$="/edit/{fixture["object_id"]}"]').click()
        context.result["actions"] += 1
        context.capture("Open greeting")
    else:
        page.locator('input[name=key]').fill(fixture["name"])
        context.result["actions"] += 1
        context.capture("Enter variable name")
    page.locator('textarea[name=value]').fill(fixture["expected"])
    context.result["actions"] += 1
    context.capture("Replace variable value")
    page.get_by_role("button", name="Update" if fixture["category"] == "target" else "Save", exact=True).click()
    settle(page)
    context.result["actions"] += 1
    context.capture("Submit variable form")
    path = "/index.php/admin/extend/variables" + ("/edit/" + fixture["object_id"] if fixture["category"] == "target" else "")
    page.goto(origin + path, wait_until="networkidle")
    context.result["actions"] += 1
    context.capture("Reopen saved variable")
    if fixture["category"] == "target":
        actual = page.locator('textarea[name=value]').input_value()
    else:
        row = page.locator(f'a[href$="/edit/{fixture["object_id"]}"]')
        actual = fixture["expected"] if row.count() and fixture["expected"] in row.inner_text() else "missing"
    context.result.update(agent_verdict="pass" if actual == fixture["expected"] else "fail",
                          termination_reason="script_completed", observation=actual,
                          evidence_steps=[context.result["actions"]])
