from dataclasses import asdict, dataclass, field
from decimal import Decimal
from email import policy
from email.parser import BytesParser
import json
import re
from urllib.parse import parse_qs, urlparse
import time


@dataclass
class Evidence:
    healthy: bool = True
    preconditions: bool = False
    submitted: bool = False
    response_valid: bool = False
    reopened: bool = False
    same_object: bool = False
    expected: str = ""
    actual: str | None = None
    object_id: int | None = None
    events: list = field(default_factory=list)


def judge(evidence: Evidence):
    if not evidence.healthy:
        return "infra_error", "environment_failure"
    if not (evidence.preconditions and evidence.submitted and evidence.response_valid):
        return "inconclusive", "trigger_not_verified"
    if not (evidence.reopened and evidence.same_object and evidence.actual is not None):
        return "inconclusive", "observation_not_completed"
    if evidence.actual == evidence.expected:
        return "pass", "persisted_value_matches"
    return "fail", "persisted_value_differs"


class ProfileObserver:
    """Read-only evaluator. No values from here enter the model context."""

    def __init__(self, page, fixture):
        self.page = page
        self.fixture = fixture
        self.evidence = Evidence(expected=fixture["expected"], object_id=fixture["object_id"])
        self.step = 0
        self.submitted_at = None
        self.reopened_at = None
        page.on("response", self._response)

    def capture(self, step, screenshot):
        self.step = step
        if urlparse(self.page.url).path != "/admin/blogger.php":
            return
        username = self.page.locator("input[name=username]")
        nickname = self.page.locator("input[name=name]")
        if not username.count() or not nickname.count():
            return
        same = username.input_value() == self.fixture["username"]
        value = nickname.input_value()
        if step == 0:
            self.evidence.preconditions = same and value != self.fixture["expected"]
        elif same and not self.evidence.reopened and self.reopened_at is not None and self.reopened_at > self.submitted_at and in_view(nickname):
            self.evidence.reopened = True
            self.evidence.same_object = same
            self.evidence.actual = value
            self.evidence.events.append({"kind": "reopened_ui", "step": step,
                "object_id": self.fixture["object_id"], "same_object": same, "actual": value, "screenshot": screenshot})

    def data(self):
        return asdict(self.evidence)

    def _response(self, response):
        request = response.request
        url = urlparse(response.url)
        if url.path != "/admin/blogger.php":
            return
        now = time.monotonic()
        if request.method == "GET" and request.is_navigation_request() and self.submitted_at is not None:
            self.reopened_at = now
        if request.method != "POST" or parse_qs(url.query).get("action") != ["update"]:
            return
        fields = request_fields(request)
        correct = fields.get("name") == self.fixture["expected"]
        self.submitted_at = now
        self.reopened_at = None
        self.evidence.submitted = correct
        self.evidence.reopened = False
        self.evidence.actual = None
        self.evidence.same_object = False
        body = response.json()
        accepted = response.status == 200 and body.get("code") == 0
        known_rejection = response.status == 400 and body.get("msg") == "Username has been set and cannot be modified"
        self.evidence.response_valid = accepted or known_rejection
        self.evidence.events.append({"kind": "submit_response", "step": self.step + 1,
            "object_id": self.fixture["object_id"], "submitted_value": fields.get("name"),
            "status": response.status, "accepted": accepted, "business_rejection": known_rejection,
            "elapsed_marker": now})


def in_view(locator):
    box = locator.bounding_box()
    size = locator.page.viewport_size
    return box is not None and 0 <= box["x"] + box["width"] / 2 < size["width"] and 0 <= box["y"] + box["height"] / 2 < size["height"]


def request_fields(request):
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + request.post_data_buffer)
        return {part.get_param("name", header="content-disposition"):
                part.get_payload(decode=True).decode("utf-8") for part in message.iter_parts()
                if part.get_content_disposition() == "form-data"}
    if content_type.startswith("application/json"):
        fields = json.loads(request.post_data)
        if not isinstance(fields, dict):
            raise ValueError("business submission must be a JSON object")
        return fields
    return {key: values[-1] for key, values in parse_qs(request.post_data or "", keep_blank_values=True).items()}


class ProviderObserver:
    def __init__(self, page, fixture):
        self.page = page
        self.fixture = fixture
        self.evidence = Evidence(expected=fixture["expected"], object_id=fixture["object_id"])
        self.step = 0
        self.submitted_at = None
        self.reopened_at = None
        page.on("response", self._response)

    def capture(self, step, screenshot):
        self.step = step
        if urlparse(self.page.url).path != "/index.php/providers":
            return
        identity = self.page.locator("input.record-id")
        field = self.page.locator(self.fixture["selector"])
        if not identity.count() or not field.count() or not in_view(field):
            return
        same = identity.input_value() == str(self.fixture["object_id"])
        value = field.input_value()
        if self.submitted_at is None and same and value != self.fixture["expected"]:
            self.evidence.preconditions = True
        if same and not self.evidence.reopened and self.submitted_at is not None and self.reopened_at is not None and self.reopened_at > self.submitted_at:
            self.evidence.reopened = True
            self.evidence.same_object = same
            self.evidence.actual = value
            self.evidence.events.append({"kind": "reloaded_ui", "step": step,
                "object_id": identity.input_value(), "same_object": same, "actual": value, "screenshot": screenshot})

    def data(self):
        return asdict(self.evidence)

    def _response(self, response):
        request = response.request
        path = urlparse(response.url).path
        now = time.monotonic()
        if path == "/index.php/providers/search" and self.submitted_at is not None and response.status == 200:
            self.reopened_at = now
        if path != "/index.php/providers/update" or request.method != "POST":
            return
        fields = request_fields(request)
        value = fields.get(f"provider[{self.fixture['field']}]")
        identity = fields.get("provider[id]")
        self.submitted_at = now
        self.reopened_at = None
        self.evidence.submitted = value == self.fixture["expected"] and identity == str(self.fixture["object_id"])
        self.evidence.reopened = False
        self.evidence.actual = None
        self.evidence.same_object = False
        body = response.json()
        self.evidence.response_valid = response.status == 200 and str(body.get("id")) == str(self.fixture["object_id"])
        self.evidence.events.append({"kind": "submit_response", "step": self.step + 1,
            "object_id": identity, "submitted_value": value, "status": response.status,
            "accepted": self.evidence.response_valid, "elapsed_marker": now})


def parse_usd(text):
    value = text.strip()
    if not re.fullmatch(r"\$(?:\d{1,3}(?:,\d{3})*|\d+)\.\d{2}", value):
        raise ValueError(f"not a USD amount in the configured display locale: {value!r}")
    return Decimal(value[1:].replace(",", ""))


class ItemObserver:
    def __init__(self, page, fixture):
        self.page = page
        self.fixture = fixture
        self.evidence = Evidence(expected=fixture["expected"])
        self.step = 0
        self.submitted_at = None
        self.reopened_at = None
        page.on("response", self._response)

    def capture(self, step, screenshot):
        self.step = step
        path = urlparse(self.page.url).path
        if step == 0 and path == "/1/common/items/create":
            self.evidence.preconditions = self.page.locator("input[name=name]").input_value() == ""
        if path != "/1/common/items" or self.reopened_at is None or self.evidence.reopened:
            return
        row = self.page.locator('tr[data-table-list][href$="/edit"]').filter(has_text=self.fixture["name"])
        if row.count() != 1:
            return
        name = row.locator("td").nth(1).locator("[data-truncate-marquee]").first.inner_text().strip()
        if name != self.fixture["name"]:
            return
        price = row.locator("td").nth(4).locator("[data-truncate-marquee]").first
        if not in_view(price):
            return
        raw = price.inner_text().strip()
        self.evidence.object_id = int(row.get_attribute("href").rsplit("/", 2)[1])
        self.evidence.actual = str(parse_usd(raw))
        self.evidence.same_object = True
        self.evidence.reopened = True
        self.evidence.events.append({"kind": "saved_item_list", "step": step, "raw_display": raw,
            "actual": self.evidence.actual, "object_id": self.evidence.object_id, "screenshot": screenshot})

    def data(self):
        return asdict(self.evidence)

    def _response(self, response):
        request = response.request
        path = urlparse(response.url).path
        if path != "/1/common/items":
            return
        if request.method == "GET" and request.is_navigation_request() and self.submitted_at is not None:
            self.reopened_at = time.monotonic()
        if request.method != "POST":
            return
        fields = request_fields(request)
        self.submitted_at = time.monotonic()
        self.reopened_at = None
        self.evidence.reopened = False
        self.evidence.actual = None
        self.evidence.object_id = None
        self.evidence.same_object = False
        self.evidence.submitted = fields.get("name") == self.fixture["name"] and fields.get("sale_price") == self.fixture["input_amount"]
        # The app navigates immediately after its AJAX response; CDP can discard that response body.
        # A unique saved row on this initially empty database supplies the persisted object identity.
        self.evidence.response_valid = response.status == 200
        self.evidence.events.append({"kind": "submit_response", "step": self.step + 1,
            "submitted_value": fields.get("sale_price"), "object_name": fields.get("name"),
            "object_id": self.evidence.object_id, "status": response.status,
            "accepted": self.evidence.response_valid, "elapsed_marker": self.submitted_at})
