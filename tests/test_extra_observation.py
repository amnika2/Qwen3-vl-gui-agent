from playwright.sync_api import sync_playwright
from types import SimpleNamespace
import pytest

from guiqa.extra_cases import PlaylistObserver
from guiqa.oracle import judge


def test_ampache_detail_container_is_not_a_playlist_table_row():
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.route("http://guiqa.test/browse.php", lambda route: route.fulfill(
                content_type="text/html", body='<table><tr id="smartplaylist_row_20"><td class="cel_playlist">Rename Me Smart</td></tr></table>'))
            page.goto("http://guiqa.test/browse.php")
            observer = PlaylistObserver(page, {"category": "target", "object_id": 20, "expected": "Renamed Smart Core4426"})
            observer.capture(0, "list.png")
            assert observer.evidence.preconditions
            page.set_content('<div id="smartplaylist_row_20"><h1>Rename Me Smart</h1></div>')
            observer.capture(1, "detail.png")
            assert judge(observer.evidence) == ("inconclusive", "trigger_not_verified")


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_ampache_fresh_ajax_list_is_valid_saved_evidence(method):
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.route("http://guiqa.test/search.php", lambda route: route.fulfill(
                content_type="text/html", body='<button id="savesearchbutton">Save as Smart Playlist</button>'))
            page.goto("http://guiqa.test/search.php")
            observer = PlaylistObserver(page, {"category": "preservation", "username": "qa-user", "expected": "created"})
            observer.capture(0, "initial.png")
            request = SimpleNamespace(method="POST", headers={"content-type": "application/x-www-form-urlencoded"}, post_data="action=save_as_smartplaylist")
            observer._response(SimpleNamespace(request=request, url="http://guiqa.test/search.php", status=200))
            if method == "GET":
                page.evaluate("location.hash='browse.php?action=smartplaylist'")
            page.set_content('<table><tr id="smartplaylist_row_21"><td class="cel_playlist">qa-user - 11/7/26</td></tr></table>')
            observer._response(SimpleNamespace(request=SimpleNamespace(method=method, headers={}, post_data=""), status=200,
                url="http://guiqa.test/browse.php?action=smartplaylist"))
            observer.capture(2, "saved-list.png")
            assert judge(observer.evidence)[0] == "pass"
            assert observer.evidence.object_id == 21
            assert observer.evidence.events[-1]["screenshot"] == "saved-list.png"
