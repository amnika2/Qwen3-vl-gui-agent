"""Pre-registered fixture families for the five frozen test applications."""

from decimal import Decimal

from .review_scenarios import scenario_metadata


CASES = {"akaunting": "sweeper-005", "ampache": "sweeper-007", "anchor": "sweeper-008",
         "appointments": "sweeper-040", "emlog": "sweeper-041"}
SUBJECTS = ["Harbor reading", "Cedar workshop", "Museum visits", "Garden supplies", "Studio lighting",
            "Library desk", "Orchard tools", "River concert", "Market support", "Lakeside lessons",
            "Archive service", "Theatre tickets", "Coffee deliveries", "Science club", "Trail guides",
            "Textile display", "Pottery course", "Book repairs", "Autumn exhibit", "Spring schedule"]


def build_catalog():
    groups = []
    for app in CASES:
        for index in range(20):
            kind = "target" if index < 10 else "preservation" if index < 15 else "blocked"
            variant = "saved" if kind != "blocked" else "cancelled" if index < 18 else "input_not_established"
            builds = ["A", "B"] if kind == "target" else ["A" if index % 2 == 0 else "B"] if kind == "preservation" else ["B"]
            groups.append(make_group(app, index, kind, variant, builds, False))
        for index, (kind, variant, builds) in enumerate([
            ("target", "saved", ["A", "B"]), ("target", "saved", ["A", "B"]),
            ("preservation", "saved", ["A", "B"]), ("blocked", "cancelled", ["B"]),
            ("blocked", "input_not_established", ["B"])]):
            groups.append(make_group(app, index + 30, kind, variant, builds, True))
    return {"version": 1, "stage": "final-test-expanded-v1", "test_reviews": 150,
            "test_fixture_groups": 100, "calibration_reviews": 40, "groups": groups}


def make_group(app, index, kind, variant, builds, calibration):
    tag = "calibration" if calibration else "test"
    identifier = f"final-{app}-{tag}-{index + 1:02d}"
    subject = SUBJECTS[index % len(SUBJECTS)]
    title = subject + (" calibration" if calibration else "")
    workflow = "preservation" if kind == "preservation" else "target"
    fixture = {"title": title}
    if app == "akaunting":
        amount = Decimal(1200000 + index * 7319) + Decimal("0.25") if workflow == "target" else Decimal(1200 + index * 31) + Decimal("0.75")
        integer, decimal = f"{amount:.2f}".split(".")
        grouped = f"{int(integer):,}".replace(",", ".") + "," + decimal
        fixture.update(name=title + " product", input_amount=grouped, expected=f"{amount:.2f}")
        initial, changes = {"catalog": "empty"}, {"Name": fixture["name"], "Sale Price": grouped, "expected_sale_price": fixture["expected"]}
        task = (f"Create a product named exactly {fixture['name']!r}. Set Sale Price to {grouped!r}, "
                f"complete the required form, and Save. Reopen the Items list and inspect the same named product. "
                f"Its sale price must represent the full amount ${amount:,.2f}. Report pass when it matches, "
                "fail for an accepted save with a different amount, or blocked when the requested save is not established.")
    elif app == "ampache":
        fixture.update(initial_name=title + " source playlist", expected=title + " final playlist", category=workflow)
        if workflow == "preservation":
            fixture["expected"] = "created"
            task = (f"On Song search for the {title!r} session, use Save as Smart Playlist once. "
                    "Continue to Smart Playlists and confirm a newly created row with the automatic name "
                    "beginning with the signed-in username. No name typing is needed. Inspect the new row after reload.")
            changes = {"new_playlist": "automatic name beginning with signed-in username"}
        else:
            task = (f"Find Smart Playlist {fixture['initial_name']!r}. Edit its Name to exactly {fixture['expected']!r} "
                    "and Save. Reopen Smart Playlists and inspect the same playlist. Report pass if the rename "
                    "persists, fail for an accepted rename that leaves a different name, or blocked if saving is not established.")
            changes = {"Name": fixture["expected"]}
        initial = {"playlist_name": fixture["initial_name"], "search_session": title}
    elif app == "anchor":
        fixture.update(name=f"ft{index + 1:02d}" + ("c" if calibration else "t"),
                       initial_value=title + " original notice", expected=title + " revised notice", category=workflow)
        fixture["object_id"] = "custom_" + fixture["name"]
        initial = {"variable_key": fixture["name"], "value": fixture["initial_value"]} if workflow == "target" else {"variable_absent": fixture["name"]}
        changes = {"Name": fixture["name"], "Value": fixture["expected"]}
        task = (f"{'Edit the existing' if workflow == 'target' else 'Create a new'} Site Variable named {fixture['name']!r}. "
                + (f"Its original Value is {fixture['initial_value']!r}; keep Name unchanged. " if workflow == "target" else "")
                + f"Set Value to exactly {fixture['expected']!r}, {'Update' if workflow == 'target' else 'Save'}, "
                "then reopen that variable and inspect its full Value. The ordinary valid save should succeed. "
                "Report pass if it persists, fail for a demonstrated business rejection or a saved mismatch, "
                "or blocked if the input or outcome cannot be established.")
    elif app == "appointments":
        field = "phone_number" if workflow == "preservation" else "mobile_number"
        fixture.update(object_id=2, field=field, selector="#phone-number" if workflow == "preservation" else "#mobile-number",
                       provider_name=subject.split()[0] + " " + ("Calibration" if calibration else "Provider"),
                       provider_email=f"provider{index + 1}{'c' if calibration else 't'}@final.guiqa.test",
                       initial_value="" if index % 2 == 0 else f"+1555200{index:04d}",
                       expected=f"+1555300{index:04d}")
        label = "Phone Number" if field == "phone_number" else "Mobile Number"
        initial = {"provider_name": fixture["provider_name"], "provider_email": fixture["provider_email"], label: fixture["initial_value"]}
        changes = {label: fixture["expected"]}
        task = (f"Open provider {fixture['provider_name']} ({fixture['provider_email']}). Edit {label} "
                f"from {fixture['initial_value']!r} to exactly {fixture['expected']!r}. Save, reload and reopen "
                f"the same provider. Inspect {label}. Report pass if it persisted, fail if an accepted save "
                "leaves a different value, or blocked if the requested save is not established.")
    else:
        short_name = subject.split()[0] + (" cal" if calibration else "")
        fixture.update(initial_value=short_name + " old", expected=short_name + " editor",
                       role="admin" if workflow == "preservation" else "writer",
                       object_id=1 if workflow == "preservation" else 2)
        initial = {"role": fixture["role"], "Nickname": fixture["initial_value"]}
        changes = {"Nickname": fixture["expected"], "Username": "unchanged"}
        task = (f"On the signed-in {fixture['role']}'s Profile, Nickname initially reads {fixture['initial_value']!r}. "
                f"Change only Nickname to exactly {fixture['expected']!r}; keep Username unchanged. Save and reopen "
                "the same Profile. This ordinary nickname edit should succeed. Report pass if it persisted, "
                "fail for a visible business rejection of the valid edit or a saved mismatch, "
                "or blocked if input or outcome cannot be established.")
    metadata = scenario_metadata(CASES[app], workflow, task, initial, changes, identifier)
    return {"id": identifier, "parent_scenario_id": identifier, "app": app, "case_id": CASES[app],
            "workflow": workflow, "kind": kind, "variant": variant, "builds": builds,
            "calibration": calibration, "split": "calibration" if calibration else "test",
            "initial": initial, "requested_changes": changes, "fixture": fixture, "task": task, **metadata}
