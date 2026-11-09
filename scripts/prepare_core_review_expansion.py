"""Freeze the four-app and Copy/Cancel catalogs for 350 train and 132 new-dev reviews."""

import argparse
import json
import shutil
from pathlib import Path

import yaml

from guiqa.sft_v2 import checksum, write_json


ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ROOT / "configs/review-expansion-v1"
RUNS = ROOT / "runs/review-expansion-v1"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dev-only", action="store_true")
    parser.add_argument("--php-404-split", action="store_true")
    args = parser.parse_args()
    CONFIGS.mkdir(parents=True, exist_ok=True)
    RUNS.mkdir(parents=True, exist_ok=True)
    if args.php_404_split:
        original = yaml.safe_load((CONFIGS / "core-php-main.yaml").read_text())
        for suffix, groups in (("a", original["groups"][::2]), ("b", original["groups"][1::2])):
            path = CONFIGS / f"core-php-main-404-{suffix}.yaml"
            with path.open("x") as output:
                yaml.safe_dump({**original, "groups": groups}, output, allow_unicode=True, sort_keys=False)
        plan = json.loads((RUNS / "core-plan.json").read_text())
        plan["diagnostic_incomplete_batches"] = ["core-php-main"]
        plan["batches"] = [batch for batch in plan["batches"] if batch["name"] != "core-php-main"]
        for suffix, port in (("a", 13432), ("b", 13433)):
            name = f"core-php-main-404-{suffix}"
            plan["batches"].append({"name": name, "catalog": name + ".yaml", "port": port,
                                    "flags": ["--include-blocked"], "raw_runs": 90,
                                    "train_reviews": 60, "dev_reviews": 30})
            plan["catalog_sha256"][name + ".yaml"] = checksum(CONFIGS / (name + ".yaml"))
        next(batch for batch in plan["batches"] if batch["name"] == "core-php-input-dev")["port"] = 13432
        write_json(RUNS / "core-plan.json", plan)
        print(json.dumps({"php_split": ["core-php-main-404-a", "core-php-main-404-b"], "ports": [13432, 13433]}))
        return
    catalogs = {
        "core-php-main.yaml": {"groups": php_groups("train", 40) + php_groups("validation", 20)},
        "core-php-input-dev.yaml": {"groups": php_groups("validation", 15, input_missing=True)},
        "core-opencart-main.yaml": {"groups": opencart_groups("train", 40) + opencart_groups("validation", 15)},
        "core-type-wolf.yaml": {"groups": type_wolf_groups()},
        "core-copy.yaml": {"case_id": "sweeper-125", "app": "opencart", "build": "B", "groups": copy_groups()},
    }
    if args.input_dev_only:
        name = "core-php-input-dev.yaml"
        path = CONFIGS / name
        shutil.copy2(path, RUNS / "core-php-input-dev-initial-catalog.yaml")
        with path.open("w") as output:
            yaml.safe_dump({"version": 1, "frozen_on": "2026-11-08", "group_ordered_variants": True,
                            **catalogs[name]}, output, allow_unicode=True, sort_keys=False)
        plan = json.loads((RUNS / "core-plan.json").read_text())
        plan["raw_runs_planned"] = 492
        plan["retention"] = "Wolf duplicate keeps B only after A/B runs; input-not-established new-dev runs B only."
        plan["catalog_sha256"][name] = checksum(path)
        next(batch for batch in plan["batches"] if batch["name"] == "core-php-input-dev")["raw_runs"] = 15
        write_json(RUNS / "core-plan.json", plan)
        print(json.dumps({"updated": name, "raw_runs": 15, "retained_reviews": 15}))
        return
    for name, document in catalogs.items():
        path = CONFIGS / name
        with path.open("x") as output:
            yaml.safe_dump({"version": 1, "frozen_on": "2026-11-08", "group_ordered_variants": True,
                            **document}, output, allow_unicode=True, sort_keys=False)
    plan = {
        "training_reviews_planned": 350, "new_development_reviews_planned": 132,
        "raw_runs_planned": 492, "retained_reviews_planned": 482,
        "training_started": False, "teacher_model_calls": 0,
        "retention": "Wolf duplicate keeps B only after A/B runs; input-not-established new-dev runs B only.",
        "catalog_sha256": {name: checksum(CONFIGS / name) for name in catalogs},
        "batches": [
            {"name": "core-php-main", "catalog": "core-php-main.yaml", "port": 13420,
             "flags": ["--include-blocked"], "raw_runs": 180, "train_reviews": 120, "dev_reviews": 60},
            {"name": "core-php-input-dev", "catalog": "core-php-input-dev.yaml", "port": 13420,
             "flags": ["--input-not-established-only"], "raw_runs": 15, "train_reviews": 0, "dev_reviews": 15},
            {"name": "core-opencart-main", "catalog": "core-opencart-main.yaml", "port": 13421,
             "flags": ["--include-blocked"], "raw_runs": 165, "train_reviews": 120, "dev_reviews": 45},
            {"name": "core-type-wolf", "catalog": "core-type-wolf.yaml", "port": 13422,
             "flags": [], "raw_runs": 112, "train_reviews": 90, "dev_reviews": 12},
            {"name": "core-copy", "catalog": "core-copy.yaml", "port": 13423,
             "flags": [], "raw_runs": 20, "train_reviews": 20, "dev_reviews": 0},
        ],
    }
    write_json(RUNS / "core-plan.json", plan)
    print(json.dumps(plan, ensure_ascii=False, indent=2))


def php_groups(partition, count, input_missing=False):
    themes = ({"train": ["parcel sorting", "archive lending", "workshop tools", "storeroom inventory",
                          "service counter", "vehicle scanning", "battery monitoring", "print dispatch"],
               "validation": ["museum tickets", "clinic queue", "vineyard weather", "rainfall survey",
                              "concert entrance", "river sampling", "garden irrigation", "theatre access"]}
              [partition] if not input_missing else ["wind tunnel", "harbour tide", "orchard gate",
                                                    "aquarium feed", "geology room"])
    token = "train" if partition == "train" else "dev-input" if input_missing else "dev"
    groups = []
    for index in range(count):
        theme = themes[index % len(themes)]
        field = "description" if index % 2 == 0 else "hostname"
        identifier = f"rex1-{token}-php-{index + 1:03d}"
        fixture = {"object_id": 1 if index % 2 == 0 else 2,
                   "address": "10.10.1.3" if index % 2 == 0 else "10.10.1.4",
                   "description": theme.title() + f" station {index + 21}",
                   "hostname": theme.replace(" ", "-") + f"-{index + 21}.lab.local", "changes": {field: ""}}
        group = {"app": "phpipam", "id": identifier, "parent_scenario_id": identifier,
                 "family": "clear_" + field, "template_family": theme.replace(" ", "_"),
                 "split": partition, "fixture": fixture}
        if input_missing:
            group["retain_builds"] = ["B"]
            group["builds"] = ["B"]
        groups.append(group)
    return groups


def opencart_groups(partition, count):
    fractions = ["125", "375", "625", "875", "15", "35", "65", "85", "0625", "1875", "4375", "8125"]
    groups = []
    for index in range(count):
        identifier = f"rex1-{'train' if partition == 'train' else 'dev'}-option-{index + 1:03d}"
        integer = (11 if partition == "train" else 61) + index // len(fractions)
        groups.append({"app": "opencart", "id": identifier, "parent_scenario_id": identifier,
                       "family": "decimal_weight", "template_family": "shipping_options" if partition == "train" else "display_options",
                       "split": partition, "fixture": {
                           "option_label": ["Green", "Blue", "Red", "Yellow"][index % 4],
                           "initial_weight": str(31 + index), "initial_quantity": 120 + index,
                           "values": {"weight": f"{integer}.{fractions[index % len(fractions)]}"}}})
    return groups


def type_wolf_groups():
    train = ["Parcel dispatch", "Archive opening", "Workshop stock", "Repair schedule", "Warehouse access",
             "Branch hours", "Library bookings", "Dock timetable", "Office delivery", "Supply register",
             "Equipment loans", "Shift handover", "Service coverage", "Inventory audit", "Safety briefing",
             "Support hours", "Resource loans", "Vehicle booking", "Storage access", "Dispatch routes"]
    dev = ["Museum tickets", "Clinic bookings", "Rainfall update"]
    types, edits, duplicates = [], [], []
    for partition, titles in (("train", train), ("validation", dev)):
        token = "train" if partition == "train" else "dev"
        for index, title in enumerate(titles):
            identifier = f"rex1-{token}-header-{index + 1:03d}"
            types.append({"app": "typesetter", "id": identifier, "parent_scenario_id": identifier,
                          "family": "consecutive_header_saves", "split": partition,
                          "fixture": {"first": title + " draft", "input": title + " final"}})
            identifier = f"rex1-{token}-page-{index + 1:03d}"
            edits.append({"app": "wolf", "id": identifier, "parent_scenario_id": identifier,
                          "family": "edit_own_slug", "split": partition,
                          "fixture": {"kind": "edit", "initial_title": title + " notice",
                                      "fields": {"title": title + " updated"}}})
    for index, title in enumerate(train[:10]):
        identifier = f"rex1-train-duplicate-{index + 1:03d}"
        duplicates.append({"app": "wolf", "id": identifier, "parent_scenario_id": identifier,
                           "family": "expected_duplicate_slug_rejection", "split": "train", "retain_builds": ["B"],
                           "fixture": {"kind": "duplicate", "initial_title": title + " original",
                                       "fields": {"title": title + " copy"}}})
    return [types[0], edits[0], duplicates[0], *types[1:], *edits[1:], *duplicates[1:]]


def copy_groups():
    names = ["Harbour task lamp", "Archive reading stand", "Workshop storage tray", "Parcel label holder",
             "Reception document rack", "Library desk organiser", "Dispatch cable shelf", "Warehouse clip board",
             "Office corner light", "Equipment check panel"]
    return [{"id": f"rex1-train-copy-{index + 1:03d}", "parent_scenario_id": f"rex1-train-copy-{index + 1:03d}",
             "split": "train", "name": name, "model": f"REX-COPY-{index + 101}", "edited_name": name + " revised"}
            for index, name in enumerate(names)]


if __name__ == "__main__":
    main()
