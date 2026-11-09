"""Freeze new business fixtures before collecting the five extended training apps."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONTEXTS = [
    ("museum", "Museum badge", "The visitor badge panel loses the selected entrance.", "Check the west entrance badge queue.", "Bamyan"),
    ("garden", "Garden booking", "The workshop booking shows the previous room.", "Book the seed exchange in the greenhouse.", "Tirana"),
    ("harvest", "Harvest delivery", "The delivery window omits the collection address.", "Use the orchard loading gate for collections.", "Algiers"),
    ("marina", "Marina permit", "The berth permit preview drops the vessel length.", "Collect the permit at the marina office.", "Pago Pago"),
    ("trail", "Trail access", "The access map resets the selected walking route.", "Start the guided walk from the south shelter.", "Andorra la Vella"),
    ("library", "Library shelf", "The shelf request form loses its collection code.", "Return reserved volumes to the staffed desk.", "Kabul"),
    ("market", "Market stall", "The stall allocation card repeats the old location.", "Unload stall equipment beside the covered hall.", "Durres"),
    ("clinic", "Clinic visit", "The visit confirmation hides the room number.", "Report to reception before the health session.", "Oran"),
    ("rehearsal", "Rehearsal slot", "The rehearsal schedule omits the piano request.", "Reserve the rehearsal studio for the quartet.", "Fagatogo"),
    ("ferry", "Ferry cargo", "The cargo docket reverts the selected sailing.", "Present the cargo label at the ferry desk.", "Encamp"),
    ("weather", "Weather sensor", "The sensor detail resets the alert threshold.", "Record the sensor reading before calibration.", "Herat"),
    ("festival", "Festival entry", "The entry confirmation loses the companion count.", "Exchange the booking code at the festival gate.", "Vlore"),
    ("bicycle", "Bicycle locker", "The locker assignment displays the earlier rack.", "Leave the bicycle key at the transport desk.", "Constantine"),
    ("archive", "Archive scan", "The scan request drops its document reference.", "Use the archive reading room for scanned records.", "Tafuna"),
    ("observatory", "Observatory tour", "The tour ticket omits the selected telescope.", "Meet the astronomer inside the east dome.", "La Massana"),
    ("bakery", "Bakery order", "The order receipt repeats the previous pickup time.", "Collect reserved bread from the side counter.", "Mazar"),
    ("sailing", "Sailing lesson", "The lesson overview loses the equipment size.", "Check the life jacket size before the lesson.", "Shkoder"),
    ("wildlife", "Wildlife survey", "The survey sheet omits the observation sector.", "Record the bird survey from the river hide.", "Annaba"),
    ("repair", "Repair ticket", "The ticket header reverts its equipment category.", "Leave labelled equipment at the repair bench.", "Aua"),
    ("choir", "Choir seating", "The seating preview drops the accessible aisle.", "Keep the accessible choir aisle clear.", "Ordino"),
]
DEVELOPMENT = [
    ("reservoir", "Reservoir inspection", "The inspection page loses the overflow setting.", "Inspect the eastern spillway control.", "Kandahar"),
    ("tram", "Tram timetable", "The service card omits the requested platform.", "Meet the tram inspection team at the depot.", "Berat"),
    ("quarry", "Quarry shipment", "The shipment card reverts the safety checklist.", "Collect the checklist at the quarry weighbridge.", "Setif"),
]
COUNTRIES = [("AF", "Afghanistan"), ("AL", "Albania"), ("DZ", "Algeria"), ("AS", "American Samoa"), ("AD", "Andorra")]


def main():
    groups = []
    for app in ("mantis", "impress", "litecart", "dokuwiki", "flatpress"):
        for split, contexts in (("train", CONTEXTS), ("validation", DEVELOPMENT if app in {"mantis", "impress", "litecart"} else [])):
            for index, content in enumerate(contexts):
                groups.append(make_group(app, split, index, content))
    path = ROOT / "configs/review-expansion-extended-v1.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "frozen_before_collection": True,
        "quota": {"train": 160, "validation": 18}, "groups": groups}, allow_unicode=True, sort_keys=False))
    print(path)


def make_group(app, split, index, content):
    slug, title, problem, detail, city = content
    group = {"app": app, "parent_scenario_id": f"expanded-v1-{app}-{slug}", "data_split": split,
             "workflow": "target", "family": "", "builds": ["A", "B"] if app in {"mantis", "impress", "litecart"} else ["A" if index % 2 == 0 else "B"]}
    if app == "mantis":
        fields = ["summary", "description"] if index % 2 == 0 else ["description", "summary"]
        fixture = {"version": "1.1.0", "summary": title + " loses saved details", "description": problem + " " + detail,
                   "field_order": fields}
        if index % 3 == 2:
            fixture["steps_to_reproduce"] = "1. Open the entry.\n2. Change the selection.\n3. Submit and reopen."
            fixture["field_order"] = [*fields, "steps_to_reproduce"]
        group.update(family="released_version_with_steps" if "steps_to_reproduce" in fixture else "released_version_multifield", fixture=fixture)
    elif app == "impress":
        keys = (["sitename"], ["slogan"], ["adminmail"], ["sitename", "slogan"], ["adminmail", "sitename"], ["sitename", "slogan", "adminmail"])[index % 6]
        values = {"sitename": title + " Centre", "slogan": detail.removesuffix("."), "adminmail": slug + "-desk@service.example.test"}
        initial = {"sitename": title + " Office", "slogan": "Information is being revised", "adminmail": slug + "-old@service.example.test"}
        group.update(family="preferences_" + "_".join(keys), fixture={"values": {key: values[key] for key in keys},
                     "field_order": list(reversed(keys)) if index % 2 else keys, "initial": initial,
                     "start": "list" if index % 2 else "editor"})
    elif app == "litecart":
        code, country = COUNTRIES[index % 5]
        fixture = {"code": slug[:8], "name": title + " route", "description": detail,
                   "country_code": code, "country": country, "country_index": {"AF": 1, "AL": 3, "DZ": 4, "AS": 5, "AD": 6}[code],
                   "city": city if index % 3 else "", "field_order": ["name", "description", "code"] if index % 2 else ["code", "name", "description"],
                   "start": "list" if index % 2 else "editor"}
        if index % 2:
            fixture["initial"] = {"code": "old-" + slug[:6], "name": title + " former route", "description": "Previous local collection note"}
        group.update(family="pending_country_city_edit" if fixture.get("initial") else "pending_country_city_create", fixture=fixture)
    else:
        scenario = {"family": "replace", "start": "editor" if index % 2 == 0 else "view" if app == "dokuwiki" else "list",
                    "edit": "replace", "initial": title + ": arrangements are pending.", "input": detail}
        mode = index % (7 if app == "dokuwiki" else 6)
        if mode == 1:
            scenario.update(family="append", edit="append", input="\n" + detail)
        elif mode == 2:
            scenario.update(family="suffix", edit="replace_suffix", initial=title + ": desk closes at 15:00.", old_suffix="15:00.", input="17:30.")
        elif mode == 3:
            scenario.update(family="preview_then_save", preview=True)
        elif mode == 4:
            scenario.update(family="consecutive_saves", intermediate=title + ": provisional location is the front desk.")
        if app == "dokuwiki":
            if mode == 5:
                scenario.update(family="summary_and_minor", summary="Publish " + slug + " instructions", minor=True)
            elif mode == 6:
                scenario.update(family="multiline", input=title + " guidance\n" + detail + "\nAsk the duty officer for assistance.", summary="Add " + slug + " guidance")
        else:
            scenario["initial_subject"] = title + " notice"
            if mode in {0, 3}:
                scenario.update(subject=title + " guide", field_order="subject_first" if index % 2 else "body_first")
            if mode == 5:
                scenario.update(family="subject_only_preserve_body", edit="keep", input="", subject=title + " information")
        group.update(family=scenario["family"], scenario=scenario)
    return group


if __name__ == "__main__":
    main()
