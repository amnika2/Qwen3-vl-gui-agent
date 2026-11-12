"""Browser workflows for LinkAce, Cockpit, Dotclear and Cotonti."""

import re

from .browser import settle


CASES = {
    "sweeper-101": {"split": "validation", "category": "O", "pairs": 15, "slug": "linkace"},
    "sweeper-025": {"split": "validation", "category": "L", "pairs": 15, "slug": "cockpit"},
    "sweeper-037": {"split": "validation", "category": "N", "pairs": 15, "slug": "dotclear"},
    "sweeper-027": {"split": "test", "category": "N", "pairs": 10, "slug": "cotonti"},
}
FAMILIES = {
    "sweeper-101": "linkace.inspect_existing_url_actions",
    "sweeper-025": "cockpit.delete_and_recreate_singleton",
    "sweeper-037": "dotclear.filter_posts",
    "sweeper-027": "cotonti.navigate_comments",
}


def task_fixture(spec):
    index, cid = spec["index"], spec["case_id"]
    token = f"d26-{index + 1:03d}"
    preservation = spec["condition"] == "preservation"
    if cid == "sweeper-101":
        fixture = {"url": f"https://example.com/{token}-{'active' if preservation else 'trashed'}",
                   "title": "Dataset link " + token, "preservation": preservation}
        task = (f"The link {fixture['title']!r} with URL {fixture['url']!r} "
                + ("is active." if preservation else "is in Trash.")
                + " Open Add Link, type that URL and inspect the live existing-URL warning without submitting. "
                + ("It must offer Edit for the active link." if preservation else
                   "It must offer Restore deleted Link for the trashed link."))
    elif cid == "sweeper-025":
        fixture = {"name": "sitesettings", "label": "Site Settings", "marker": "STALE_TITLE_MARKER_" + token}
        task = (f"Open Site Settings and verify its Title is {fixture['marker']!r}. Delete the singleton, "
                "then recreate sitesettings with label Site Settings and a Text field named title, labeled Title. "
                "Reopen the form and inspect the labeled Title field; deleting the old singleton must have cleared its value.")
    elif cid == "sweeper-037":
        fixture = {"preservation": preservation, "filter": "comment" if preservation else "trackback",
                   "value": "0" if preservation else "1", "open_title": "Saturday Market Notes " + token,
                   "closed_title": "Quiet Week Ahead " + token}
        fixture["expected"] = fixture["closed_title"] if preservation else fixture["open_title"]
        fixture["excluded"] = [fixture["open_title"]] if preservation else [fixture["closed_title"], "Welcome to Dotclear"]
        task = ("Open Posts and apply " + ("Comments Closed" if preservation else "Trackbacks Opened")
                + f". Inspect the resulting entry titles: {fixture['expected']!r} must be present and "
                + ", ".join(repr(title) for title in fixture["excluded"]) + " must be absent.")
    else:
        fixture = {"preservation": preservation, "control": "pagenav_next" if preservation else "pagenav_last",
                   "expected": ("PAGE2_ONLY_MARKER_" if preservation else "LASTPAGE_ONLY_MARKER_") + token,
                   "first_marker": "PAGE1_ONLY_MARKER_" + token, "token": token}
        task = (f"Open the Welcome page comments and confirm {fixture['first_marker']} is visible. "
                + ("Click Next (>), the single-arrow control." if preservation else
                   "Click Last (>>), the double-arrow control.")
                + f" Inspect the comments after that click; {fixture['expected']} must be visible.")
    return task, fixture


def parameterize_fixture(instance, spec, fixture):
    if spec["case_id"] == "sweeper-101":
        import sqlite3
        database = instance.directory / "writable/tmp/linkace-app/database/database.sqlite"
        with sqlite3.connect(database) as connection:
            user_id = connection.execute("SELECT id FROM users WHERE email=?", ("quentin.marsh@inkwell.test",)).fetchone()[0]
            connection.execute("INSERT INTO links (user_id,url,title,created_at,updated_at,deleted_at) VALUES (?,?,?,?,?,?)",
                               (user_id, fixture["url"], fixture["title"], "2026-11-12 10:00:00", "2026-11-12 10:00:00",
                                None if fixture["preservation"] else "2026-11-12 10:01:00"))
    elif spec["case_id"] == "sweeper-025":
        program = ("chdir(" + repr(instance.live_app) + "); $_SERVER['HTTP_HOST']=" + repr(instance.url.removeprefix("http://"))
                   + "; $_SERVER['SCRIPT_NAME']='/index.php'; require 'bootstrap.php'; "
                   + "$data=json_decode($argv[1],true); cockpit()->module('singletons')->saveData('sitesettings',['title'=>$data['marker']]);")
        import json
        instance.run(["php", "-r", program, json.dumps(fixture)])
    elif spec["case_id"] == "sweeper-037":
        import sqlite3
        with sqlite3.connect(instance.live_db) as connection:
            connection.execute("UPDATE dc_post SET post_title=? WHERE post_title=?", (fixture["open_title"], "Saturday Market Notes"))
            connection.execute("UPDATE dc_post SET post_title=? WHERE post_title=?", (fixture["closed_title"], "Quiet Week Ahead"))
    elif spec["case_id"] == "sweeper-027":
        sql = "UPDATE cot_com SET com_text="
        expression = "com_text"
        for marker in ("PAGE1_ONLY_MARKER", "PAGE2_ONLY_MARKER", "LASTPAGE_ONLY_MARKER"):
            expression = "REPLACE(" + expression + ",'" + marker + "','" + marker + "_" + fixture["token"] + "')"
        instance.run(["mysql", "--protocol=TCP", "--host=127.0.0.1", "--port=" + str(instance.db_port),
                      "-uroot", "cotonti", "-e", sql + expression + ";"])


def prepare_login(page, url, case_id):
    if case_id == "sweeper-101":
        page.goto(url + "/login", wait_until="domcontentloaded")
        page.locator('input[name="email"]').fill("quentin.marsh@inkwell.test")
        page.locator('input[name="password"]').fill("InkShelf7!maple")
        page.locator('button[type="submit"]').click()
        page.wait_for_url(lambda value: "/login" not in str(value))
    elif case_id == "sweeper-025":
        page.goto(url + "/auth/login", wait_until="domcontentloaded")
        page.locator('input[type="text"]').fill("admin")
        page.locator('input[type="password"]').fill("admin")
        page.get_by_role("button", name="Authenticate", exact=True).click()
        page.wait_for_url(lambda value: "/auth/login" not in str(value))
    elif case_id == "sweeper-037":
        page.goto(url + "/admin/auth.php", wait_until="domcontentloaded")
        page.locator('input[name="user_id"]').fill("nell.briggs")
        page.locator('input[name="user_pwd"]').fill("amberWick8!")
        page.locator('input[type="submit"], button[type="submit"]').click()
        page.wait_for_url(lambda value: "/auth.php" not in str(value))
    else:
        page.goto(url + "/login.php", wait_until="domcontentloaded")
        page.locator('input[name="rusername"]').fill("ida-crowe")
        page.locator('input[name="rpassword"]').fill("amber7silt")
        page.locator('button[name="rlogin"]').click()
        page.wait_for_url(lambda value: "/login.php" not in str(value))
    settle(page)


def linkace_flow(r, url, fixture):
    from .dataset26_webapps import check, finish

    page = r.page
    r.goto(url + "/links/create")
    r.click(page.locator('input[name="url"]'))
    r.press("Control+a")
    r.step({"action": "type", "text": fixture["url"], "input_method": "keyboard"})
    r.press("Tab")
    page.wait_for_timeout(1500)
    r.capture("Inspect live existing-URL warning")
    warning = page.locator(".link-exists")
    restore = warning.locator(".link-exists-restore").is_visible()
    edit = warning.get_by_role("link", name="Edit Link", exact=True).is_visible()
    value = page.locator('input[name="url"]').input_value()
    offered = edit and not restore if fixture["preservation"] else restore
    checks = [check("url_entered", fixture["url"], value, value == fixture["url"], r.index),
              check("existing_url_action", "Edit" if fixture["preservation"] else "Restore deleted Link",
                    {"restore_visible": restore, "edit_visible": edit}, offered, r.index)]
    return finish(r, checks, [r.index], f"URL field={value!r}; Restore visible={restore}; Edit visible={edit}.")


def cockpit_flow(r, url, fixture):
    from .dataset26_webapps import check, finish

    page = r.page
    r.goto(url + "/singletons")
    r.click(page.get_by_text(fixture["label"], exact=True))
    title = page.locator('cp-fieldcontainer:has(label[title="title"]) input[type="text"]')
    title.wait_for()
    initial = title.input_value()
    if initial != fixture["marker"]:
        raise ValueError("Cockpit initial Title differs from the official seeded marker")
    initial_step = r.index
    r.goto(url + "/singletons")
    card = page.locator(".uk-panel").filter(has_text=fixture["label"])
    r.click(card.locator('[data-uk-dropdown] a, [data-uk-dropdown] button').first)
    r.click(card.get_by_text("Delete", exact=True))
    r.click(page.locator(".uk-modal.uk-open .js-modal-confirm"))
    deleted = r.index
    page.get_by_text("Singleton removed", exact=False).wait_for()
    r.goto(url + "/singletons/singleton")
    r.fill(page.get_by_label("Name", exact=True), fixture["name"])
    r.fill(page.get_by_label("Label", exact=True), fixture["label"])
    r.click(page.locator('cp-fieldsmanager a:visible').filter(has_text=re.compile(r"^\s*Add field\.?\s*$")).first)
    r.fill(page.locator('cp-fieldsmanager input[placeholder="name"]'), "title")
    r.click(page.locator('cp-fieldsmanager a:has(.uk-icon-cog)').first)
    r.fill(page.locator('cp-fieldsmanager .uk-modal.uk-open input[placeholder="Label"]'), "Title")
    r.select('cp-fieldsmanager .uk-modal.uk-open select:visible', value="text")
    r.click(page.locator('cp-fieldsmanager .uk-modal.uk-open button.uk-modal-close'))
    r.press("Tab")
    r.wait(0.6)
    r.click(page.get_by_role("button", name="Save", exact=True))
    page.get_by_text("Saving successful", exact=False).wait_for()
    r.click(page.get_by_text("Show form", exact=False))
    title.wait_for()
    actual = title.input_value()
    checks = [check("title_after_delete_recreate", "", actual, actual == "", r.index)]
    return finish(r, checks, [initial_step, deleted, r.index],
                  f"Initial Title={initial!r}; recreated sitesettings Title={actual!r}.")


def dotclear_flow(r, url, fixture):
    from .dataset26_webapps import check, finish

    page = r.page
    r.goto(url + "/admin/posts.php")
    page.get_by_role("link", name=fixture["open_title"], exact=True).wait_for()
    r.click(page.locator("#filter-control"))
    r.click(page.locator(f'select[name="{fixture["filter"]}"]'))
    r.select(f'select[name="{fixture["filter"]}"]', value=fixture["value"])
    apply = page.locator('#filters-form input[type="submit"]')
    if apply.is_visible():
        r.click(apply)
    r.press("Control+r")
    table = page.locator("#form-entries").inner_text()
    checks = [check("required_entry", fixture["expected"], table, fixture["expected"] in table, r.index)]
    checks.extend(check("excluded_entry", title, table, title not in table, r.index) for title in fixture["excluded"])
    return finish(r, checks, [r.index], f"Filtered entries table: {table!r}.")


def cotonti_flow(r, url, fixture):
    from .dataset26_webapps import check, finish

    page = r.page
    r.goto(url + "/index.php?e=page&c=news")
    r.goto(url + "/index.php?e=page&c=news&id=1")
    r.scroll(600)
    page.get_by_text(fixture["first_marker"], exact=False).first.wait_for()
    r.scroll(450)
    before = r.index
    r.click(page.locator("span." + fixture["control"] + " a"))
    page.wait_for_timeout(500)
    r.capture("Read comments after pagination")
    body = page.locator(".comments-container").inner_text()
    checks = [check("destination_comment_marker", fixture["expected"], body,
                    fixture["expected"] in body, r.index)]
    return finish(r, checks, [before, r.index], f"Comments after pagination: {body!r}.")


FLOWS = {"sweeper-101": linkace_flow, "sweeper-025": cockpit_flow,
         "sweeper-037": dotclear_flow, "sweeper-027": cotonti_flow}
