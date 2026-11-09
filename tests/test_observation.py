from types import SimpleNamespace

from playwright.sync_api import sync_playwright

from guiqa.oracle import ProfileObserver, ProviderObserver, judge


def test_unsaved_provider_edit_cannot_overwrite_reloaded_server_value():
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.route("http://guiqa.test/index.php/providers", lambda route: route.fulfill(
                content_type="text/html", body='<input class="record-id" value="2"><input id="mobile-number" value="">'))
            page.goto("http://guiqa.test/index.php/providers")
            fixture = {"expected": "+15551234567", "object_id": 2, "selector": "#mobile-number", "field": "mobile_number"}
            observer = ProviderObserver(page, fixture)
            observer.capture(0, "before.png")
            request = SimpleNamespace(method="POST", headers={"content-type": "application/x-www-form-urlencoded"},
                post_data="provider%5Bid%5D=2&provider%5Bmobile_number%5D=%2B15551234567")
            observer._response(SimpleNamespace(request=request, url="http://guiqa.test/index.php/providers/update",
                status=200, json=lambda: {"id": 2}))
            observer._response(SimpleNamespace(request=SimpleNamespace(method="POST"),
                url="http://guiqa.test/index.php/providers/search", status=200))
            observer.capture(1, "reloaded-blank.png")
            page.locator("#mobile-number").fill("+15551234567")
            observer.capture(2, "unsaved-edit.png")
            assert observer.evidence.actual == ""
            assert judge(observer.evidence)[0] == "fail"
            assert observer.evidence.events[-1]["screenshot"] == "reloaded-blank.png"


def test_unsaved_profile_edit_cannot_replace_first_reopen_evidence():
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.route("http://guiqa.test/admin/blogger.php", lambda route: route.fulfill(
                content_type="text/html", body='<input name="username" value="writer"><input name="name" value="Old">'))
            page.goto("http://guiqa.test/admin/blogger.php")
            observer = ProfileObserver(page, {"username": "writer", "expected": "New", "object_id": 2})
            observer.capture(0, "before.png")
            request = SimpleNamespace(method="POST", headers={"content-type": "application/x-www-form-urlencoded"}, post_data="name=New")
            observer._response(SimpleNamespace(request=request, url="http://guiqa.test/admin/blogger.php?action=update",
                status=400, json=lambda: {"msg": "Username has been set and cannot be modified"}))
            observer._response(SimpleNamespace(request=SimpleNamespace(method="GET", is_navigation_request=lambda: True),
                url="http://guiqa.test/admin/blogger.php"))
            observer.capture(1, "reopened-old.png")
            page.locator("input[name=name]").fill("New")
            observer.capture(2, "unsaved-new.png")
            assert observer.evidence.actual == "Old"
            assert judge(observer.evidence)[0] == "fail"
