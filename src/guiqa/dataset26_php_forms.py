"""Screenshot-observed AdminerEvo, PHP Server Monitor and Flyspray workflows."""

import subprocess

from .browser import settle
from .dataset26_runtime import write_json


def mysql(instance, statement):
    try:
        return instance.run(["mysql", "--protocol=tcp", "--host=127.0.0.1", f"--port={instance.db_port}",
                             "-uroot", "--batch", "--skip-column-names"], input=statement).stdout
    except subprocess.CalledProcessError as error:
        (instance.directory / "fixture-sql-error.txt").write_text(error.stderr)
        raise


def adminerevo(page, instance, job, directory):
    from .dataset26_phpapps import recorder

    names = [f"Linen Apron {job['index']:03d}", f"Clay Mug {job['index']:03d}"]
    mysql(instance, f"USE wickbin; UPDATE listings SET name='{names[0]}' WHERE id=1; "
                    f"UPDATE listings SET name='{names[1]}' WHERE id=2;")
    page.goto(instance.url, wait_until="domcontentloaded")
    page.locator('input[name="auth[server]"]').fill("127.0.0.1:" + str(instance.db_port))
    page.locator('input[name="auth[username]"]').fill("dale.quist")
    page.locator('input[name="auth[password]"]').fill("ironKettle3!")
    page.locator('input[name="auth[db]"]').fill("wickbin")
    page.get_by_role("button", name="Login", exact=True).click()
    page.locator('a[href*="select=listings"]').first.wait_for()
    settle(page)
    task = (f"In the wickbin database, open listings and select the rows {names!r}. Open the bulk Edit form. "
            "Inspect the status ENUM dropdown without saving. Its default must be original, preserving the two "
            "existing values, rather than NULL.")
    r = recorder(page, directory, job, "bulk_edit_nullable_enum_default", task)
    r.click(page.locator('a[href*="select=listings"]').first)
    for name in names:
        row = page.locator("#table tbody tr").filter(has_text=name)
        r.click(row.locator('input[name="check[]"]'))
    r.click(page.locator('input[name="edit"]'))
    field = page.locator('select[name="fields[status]"]')
    field.wait_for()
    observed = field.locator("option:checked").inner_text().strip()
    r.click(field)
    r.press("Escape")
    checks = [{"field": "status default", "expected": "original", "observed": observed, "step": r.index}]
    write_json(directory / "private/visible-readings.json", checks)
    return r.finish("pass" if observed.casefold() == "original" else "fail",
                    f"The bulk Edit form's status dropdown visibly selects {observed!r}.", checks=checks)


def phpservermon(page, instance, job, directory):
    from .dataset26_phpapps import recorder

    expected_user = f"Tilda {job['index']:03d}"
    mysql(instance, f"USE phpservermon; UPDATE psm_users SET name='{expected_user}' WHERE user_name='tildaBenn';")
    page.goto(instance.url + "/index.php", wait_until="domcontentloaded")
    page.locator("#input-username").fill("jossPern")
    page.locator("#input-password").fill("crib8dusk")
    page.locator("button[type=submit]").click()
    page.locator("#input-username").wait_for(state="hidden")
    settle(page)
    task = (f"Compare the Permissions choices on an existing server and on Add new server. The existing user "
            f"{expected_user!r} must be available in both dropdowns. Open the dropdown lists and inspect their "
            "visible choices, using the dropdown search for that user. Do not submit a server.")
    r = recorder(page, directory, job, "add_server_permissions_population", task)
    r.goto(instance.url + "/index.php?mod=server&action=edit&id=1")
    r.click(page.locator('button[data-id="user_id"]'))
    dropdown = page.locator(".bootstrap-select > .dropdown-menu")
    dropdown.wait_for(state="visible")
    r.fill(dropdown.locator(".bs-searchbox input"), expected_user)
    existing = dropdown.inner_text()
    existing_choice = dropdown.get_by_role("option", name=expected_user, exact=True).count() > 0
    existing_step = r.index
    r.press("Escape")
    r.goto(instance.url + "/index.php?mod=server&action=edit")
    r.click(page.locator('button[data-id="user_id"]'))
    dropdown.wait_for(state="visible")
    r.fill(dropdown.locator(".bs-searchbox input"), expected_user)
    added = dropdown.inner_text()
    added_choice = dropdown.get_by_role("option", name=expected_user, exact=True).count() > 0
    added_step = r.index
    checks = [{"field": "Existing server Permissions", "expected_member": expected_user,
               "observed": existing, "choice_visible": existing_choice, "step": existing_step},
              {"field": "Add server Permissions", "expected_member": expected_user,
               "observed": added, "choice_visible": added_choice, "step": added_step}]
    write_json(directory / "private/visible-readings.json", checks)
    return r.finish("pass" if existing_choice and added_choice else "fail",
                    f"Existing-server choices: {existing!r}. Add-server choices: {added!r}.",
                    evidence_steps=[existing_step, added_step], checks=checks)


def flyspray(page, instance, job, directory):
    from .dataset26_phpapps import recorder

    target = f"Please review the sample task {job['index']:03d}."
    retained = f"Keep the documentation reminder {job['index']:03d}."
    mysql(instance, "USE flyspray; "
                    f"UPDATE flyspray_reminders SET reminder_message='{target}'; "
                    "INSERT INTO flyspray_reminders(task_id,to_user_id,from_user_id,start_time,how_often,last_sent,reminder_message) "
                    f"SELECT task_id,to_user_id,from_user_id,start_time,how_often,last_sent,'{retained}' "
                    "FROM flyspray_reminders LIMIT 1;")
    page.goto(instance.url, wait_until="domcontentloaded")
    page.locator("#show_loginbox").click()
    page.locator("#lbl_user_name").fill("sasha.kirk")
    page.locator("#lbl_password").fill("peatCrest7!")
    page.locator("#login_button").click()
    page.locator("#logoutlink").wait_for()
    settle(page)
    task = (f"Open Sample Task and Reminders. Remove only the reminder {target!r}, keeping {retained!r}. "
            "Verify the deletion confirmation, reopen Reminders and check that the target is absent and the other reminder remains.")
    r = recorder(page, directory, job, "delete_selected_reminder", task)
    task_url = instance.url + "/index.php?do=details&task_id=1"
    r.goto(task_url)
    r.click(page.locator("#remindtab a"))
    row = page.locator("#reminders tbody tr").filter(has_text=target)
    r.click(row.locator('input[name="reminder_id[]"]'))
    r.click(page.locator("#reminders button[type=submit]"))
    r.scroll(-2000)
    response_step = r.index
    response_text = page.locator("body").inner_text()
    success = "The selected reminder has been deleted." in response_text
    r.goto(task_url)
    r.click(page.locator("#remindtab a"))
    panel = page.locator("#remind")
    panel.wait_for(state="visible")
    text = panel.inner_text()
    missing = target not in text
    preserved = retained in text
    checks = [{"field": "Deletion confirmation", "expected": True, "actual": success, "step": response_step},
              {"field": "Selected reminder absent", "expected": True, "actual": missing, "step": r.index},
              {"field": "Unselected reminder retained", "expected": True, "actual": preserved, "step": r.index}]
    write_json(directory / "private/visible-readings.json", {"checks": checks, "response": response_text, "reminders": text})
    return r.finish("pass" if success and missing and preserved else "fail",
                    f"Deletion confirmation={success}; selected reminder absent={missing}; unselected reminder retained={preserved}.",
                    evidence_steps=[response_step, r.index], checks=checks)
