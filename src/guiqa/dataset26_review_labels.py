"""Public business requirements and recorded screenshot-reading labels for dataset26."""

import json
import re


# Stage numbers refer to the pinned collection workflows and their retained screenshots.
EVIDENCE_CANDIDATES = {
    "sweeper-001": {"upload_input": 2, "submit_response": 3},
    "sweeper-049": {"upload_input": 1, "upload_response": 2},
    "sweeper-196": {"original_menu": 2, "move_response": 3},
    "sweeper-055": {"original_reminders": 2, "selected_target": 3, "remove_response": 4},
}


CHECKS = {
    "sweeper-125": [
        ("option_weight", "Product option Weight", "The same option's reopened Weight equals the requested value.", "Weight")],
    "sweeper-142": [
        ("description", "Address Description", "The reopened Description satisfies its requested edit or preservation constraint.", "Description"),
        ("hostname", "Address Hostname", "The reopened Hostname satisfies its requested edit or preservation constraint.", "Hostname")],
    "sweeper-195": [
        ("title", "Page Title", "The requested Title satisfies the page's stated save or duplicate-rejection requirement.", "Title"),
        ("slug", "Page Slug", "The requested Slug satisfies the page's stated save or duplicate-rejection requirement.", "Slug")],
    "sweeper-151": [
        ("unique_field_error", "Unique field", "The duplicate unique field displays its uniqueness error.", "unique_field_error"),
        ("plain_field_has_no_unique_error", "Nonunique field", "The nonunique field has no uniqueness error.", "plain_field_has_no_unique_error")],
    "sweeper-004": [
        ("enum_default", "Bulk-edit status ENUM", "The status dropdown defaults to original without saving.", "status default")],
    "sweeper-146": [
        ("existing_permissions", "Existing-server Permissions", "The specified existing user is available in the existing-server dropdown.", "Existing server Permissions"),
        ("new_permissions", "Add-server Permissions", "The same user is available in the Add-server dropdown.", "Add server Permissions")],
    "sweeper-196": [
        ("menu_order", "First two menu entries", "The requested move swaps the two menu entries and the order survives reopening.", "Menu order")],
    "sweeper-055": [
        ("deletion_confirmation", "Reminder deletion response", "The selected reminder deletion receives its confirmation.", "Deletion confirmation"),
        ("selected_reminder_absent", "Selected reminder", "The selected reminder is absent after reopening Reminders.", "Selected reminder absent"),
        ("unselected_reminder_retained", "Unselected reminder", "The other reminder remains after reopening Reminders.", "Unselected reminder retained")],
    "sweeper-034": [
        ("allowed_media_readable", "Allowed image detail", "The allowed image detail is readable.", "Allowed media readable"),
        ("restricted_media_denied", "Restricted image access", "The restricted namespace displays Permission Denied after reload.", "Restricted media denied"),
        ("restricted_detail_absent", "Restricted image detail", "The viewable restricted media-detail section is absent after reload.", "Restricted media detail visible")],
    "sweeper-053": [
        ("ordinary_article", "Ordinary article", "Following the ordinary title displays the requested ordinary article body.", "Ordinary article body"),
        ("emoji_article", "Emoji article", "Following the emoji title displays its requested body after reload.", "Emoji article body"),
        ("no_not_found", "Article navigation response", "The article result has no page-not-found message.", "Page not found")],
    "sweeper-049": [
        ("uploaded_file", "Guest-uploaded file", "The supplied file remains in the root listing after reload and filename search.", "uploaded_file")],
    "sweeper-102": [
        ("matching_bookmark_count", "Imported bookmarks", "The matching bookmark count after URL normalization equals the count required by the public task.", "matching_bookmark_count")],
    "sweeper-083": [
        ("no_bill_type_error", "Expense submission response", "The Expense bill submission has no Bill Type validation error.", "no_bill_type_error"),
        ("new_bill_visible", "New Expense bill", "The identified new bill appears in the reopened Bills list.", "new_bill_visible")],
    "sweeper-020": [
        ("interval_validation", "Watch check interval", "The visible save response accepts the positive interval or rejects the empty/zero interval as the task requires.", "interval_validation")],
    "sweeper-101": [
        ("url_entered", "Add Link URL", "The URL field visibly equals the requested existing URL without submitting.", "url_entered"),
        ("existing_url_action", "Existing-URL warning", "The warning offers the task's Restore or Edit action for the same existing link.", "existing_url_action")],
    "sweeper-025": [
        ("title_after_delete_recreate", "Recreated singleton Title", "Deleting and recreating sitesettings clears the previously stored Title.", "title_after_delete_recreate")],
    "sweeper-037": [
        ("required_entry", "Required filtered entry", "The entry required by the selected filter is present in the resulting table.", "required_entry")],
    "sweeper-001": [
        ("saved_attachment", "Account ID Photo attachment", "The reopened account displays a saved link for the supplied filename.", "ID Photo")],
    "sweeper-005": [
        ("item_name", "New product Name", "The saved Items row identifies the newly created product by its requested exact name.", "Name"),
        ("sale_price", "New product Sale Price", "The same product's saved sale price represents the full requested amount.", "Sale Price")],
    "sweeper-192": [
        ("email_after_reload", "Preferences Email", "The reopened Email satisfies the task's saved-value or preservation requirement.", "email_after_reload"),
        ("height_after_reload", "Preferences Height", "The reopened Height satisfies the task's saved-value or preservation requirement.", "height_after_reload"),
        ("visible_validation_error", "Preferences validation response", "The visible response rejects a duplicate email and accepts the stated unique-email edit.", "visible_validation_error")],
    "sweeper-045": [
        ("quantity", "Product Qty input", "The visible Qty equals the saved inventory specified by the task, without saving.", "quantity")],
    "sweeper-009": [
        ("answer_accepted", "Question answer", "The requested answer visibly becomes Accepted under the selected permalink setting.", "answer_accepted"),
        ("answer_not_found_error", "Answer acceptance response", "The acceptance response has no Answer do not found error.", "answer_not_found_error")],
    "sweeper-027": [
        ("destination_comment_marker", "Welcome comments destination", "The requested Next or Last control reaches the comments containing the task's destination marker.", "destination_comment_marker")],
    "sweeper-043": [
        ("editor_imported_text", "Imported editor document", "The actual editor document contains the text required by the supplied import.", "editor_imported_text"),
        ("import_failure_visible", "Document import response", "The supplied document is processed without an Import failed error.", "import_failure_visible"),
        ("default_welcome_removed", "Initial welcome paragraph", "The imported document replaces the default welcome paragraph.", "default_welcome_removed")],
    "sweeper-087": [
        ("authentication_result", "InvoicePlane account authentication", "The inactive account is denied with a credentials error, or the active account reaches its dashboard, as stated by the task.", "authentication_result")],
    "sweeper-074": [
        ("transferred_stock_note", "Transferred Cookies stock entry", "After purchase and transfer, the Pantry Cookies entry retains the requested note.", "stock note")],
}


def public_requirements(case_id, task):
    specifications = list(CHECKS[case_id])
    if case_id == "sweeper-037":
        excluded = re.findall(r"'([^']*)'", task.split("must be present and", 1)[1])
        specifications.extend((f"excluded_entry_{index}", f"Filtered entry {title}",
                               "This entry is absent from the resulting filtered table.", "excluded_entry")
                              for index, title in enumerate(excluded, 1))
    if case_id == "sweeper-192" and "unique-email edit" in task:
        specifications.extend([
            ("first_name_after_reload", "Preferences First name", "The requested First name persists after reload.", "first_name_after_reload"),
            ("last_name_after_reload", "Preferences Last name", "The requested Last name persists after reload.", "last_name_after_reload"),
        ])
    return [{"id": identity, "object": target, "expected": expected}
            for identity, target, expected, _ in specifications]


def normalize_review(case_id, task, recorded):
    requirements = public_requirements(case_id, task)
    readings = _recorded_checks(case_id, recorded["checks"])
    checks = []
    for requirement in requirements:
        reading = readings[requirement["id"]]
        observed, matched = _reading_result(case_id, task, reading, recorded["observation"])
        steps = sorted({*_reading_steps(reading, recorded["evidence_steps"]),
                        *EVIDENCE_CANDIDATES.get(case_id, {}).values()})
        checks.append({**requirement, "observed": json.dumps(observed, ensure_ascii=False) if observed is not None else None,
                       "evidence_steps": steps, "status": "unknown" if matched is None else "pass" if matched else "fail"})
    statuses = [check["status"] for check in checks]
    verdict = "fail" if "fail" in statuses else "pass" if all(value == "pass" for value in statuses) else "blocked"
    steps = sorted({*recorded["evidence_steps"], *(step for check in checks for step in check["evidence_steps"])})
    return {"action": "finish", "verdict": verdict, "observation": recorded["observation"],
            "evidence_steps": steps, "checks": checks}, requirements


def _recorded_checks(case_id, checks):
    by_name = {check.get("name", check.get("field")): check for check in checks}
    result = {identity: by_name[source] for identity, _, _, source in CHECKS[case_id]
              if source in by_name}
    if case_id == "sweeper-037":
        result.update({f"excluded_entry_{index}": check
                       for index, check in enumerate((value for value in checks if value["name"] == "excluded_entry"), 1)})
    elif case_id == "sweeper-192":
        result.update({check["name"]: check for check in checks})
    elif case_id == "sweeper-049":
        result["uploaded_file"] = checks[0]
    elif case_id == "sweeper-087":
        result["authentication_result"] = checks[0]
    return result


def _reading_result(case_id, task, reading, observation):
    if "result_matches_request" in reading:
        facts = {"before": reading["before"], "after": reading["after"]}
        if case_id == "sweeper-195" and reading["after"] is None and "already exists!" in observation:
            facts["submission_observation"] = observation
            return facts, "duplicate slugs are rejected" in task
        return facts, reading["result_matches_request"]
    if "matched" in reading:
        return {"actual": reading["actual"]}, reading["matched"]
    if "choice_visible" in reading:
        return {"choices": reading["observed"], "choice_visible": reading["choice_visible"]}, reading["choice_visible"]
    if "visible_after_reload_and_search" in reading:
        value = reading["visible_after_reload_and_search"]
        return {"matching_file_visible_after_reload_and_search": value}, value
    if "saved_link_visible" in reading:
        value = reading["saved_link_visible"]
        return {"saved_attachment_link_visible": value}, value
    if case_id == "sweeper-087":
        value = reading["observed"]
        expected = "credentials rejection" if "is inactive" in task or "is deactivated" in task else "dashboard"
        return {"authentication_result": value}, value == expected if value in {"credentials rejection", "dashboard"} else None
    if "observed_row" in reading:
        return {"row": reading["observed_row"]}, reading["expected"] in reading["observed_row"]
    if "after" in reading:
        return {"before": reading["before"], "after": reading["after"]}, reading["after"] == reading["expected"]
    value = reading["actual"] if "actual" in reading else reading["observed"]
    expected = reading["expected"]
    matched = value if type(value) is bool and isinstance(expected, str) else value == expected
    return {"actual": value}, matched


def _reading_steps(reading, evidence):
    if "step" in reading:
        return [reading["step"]]
    steps = [reading[key]["step"] for key in ("before", "after")
             if isinstance(reading.get(key), dict)]
    return sorted(set([*steps, *evidence]))
