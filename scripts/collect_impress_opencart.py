"""Collect real UI trajectories for two independently deployed form apps."""
import argparse
import json
import shutil
import tarfile
import uuid
from decimal import Decimal
from pathlib import Path

import yaml

from guiqa.apps.impress_opencart import register
from guiqa.artifacts import digest, source_digest, write_json
from guiqa.cli import load_config
from guiqa.runner import run_case
from guiqa.sft_export import export

ROOT = Path(__file__).resolve().parents[1]


def scenarios(catalog):
    result = []
    for entry in yaml.safe_load(catalog.read_text())["scenarios"]:
        if entry["app"] not in {"impresscms", "opencart"} or entry["build"] not in {"A", "B"} or entry["split"] not in {"train", "validation"}:
            raise ValueError(f"{entry['id']}: invalid app, build, or dataset split")
        impress = entry["app"] == "impresscms"
        case = "sweeper-084" if impress else "sweeper-125"
        values = entry["values"]
        if impress:
            if set(values) - {"sitename", "slogan", "adminmail"} or not all(isinstance(value, str) for value in values.values()):
                raise ValueError(f"{entry['id']}: invalid General Settings fields")
            expected = json.dumps(values, ensure_ascii=False, sort_keys=True)
            fixture = {"username": "remyHolt", "password": "slateCup7!", "values": values,
                "field_order": list(values), "start": entry["start"], "expected": expected,
                "initial": {"sitename": "ImpressCMS", "slogan": "Make a lasting impression ", "adminmail": "admin@guiqa.test"},
                "matrix_expected": "fail" if entry["build"] == "A" else "pass"}
            labels = {"sitename": "Site name", "slogan": "Slogan for your site", "adminmail": "Admin mail address"}
            edits = "; ".join(f"set {labels[field]} to {value!r}" for field, value in values.items())
            task = ("In ImpressCMS General Settings, " + ("open General Settings from Preferences, then " if entry["start"] == "list" else "")
                + edits + ". Submit Go once. If the completed valid submission displays an internal/PHP fatal error, report fail. "
                  "Otherwise reopen General Settings and inspect all requested fields; report pass only if they all match, fail if saved values differ, or blocked if the operation cannot be completed.")
            workflow = "target"
        else:
            if set(values) - {"weight", "quantity"} or "weight" not in values:
                raise ValueError(f"{entry['id']}: invalid option-value fields")
            canonical = {field: str(Decimal(str(value)).normalize()) if field == "weight" else int(value)
                         for field, value in values.items()}
            expected = json.dumps(canonical, sort_keys=True)
            decimal = Decimal(str(values["weight"])) % 1 != 0
            fixture = {"username": "lina.croft", "password": "amberQuilt7!", "option_label": entry["label"],
                "values": values, "field_order": list(values), "start": entry["start"], "expected": expected,
                "initial_weight": "20", "initial_quantity": 100,
                "matrix_expected": "fail" if entry["build"] == "A" and decimal else "pass"}
            edits = "; ".join(f"set {field.title()} to {value}" for field, value in values.items())
            task = ("Edit Apple Cinema 30\" product #42 in OpenCart. "
                + ("Open the Option tab. " if entry["start"] == "product" else "The Option tab is open. ")
                + f"Open the option value editor for {entry['label']}; {edits}. Keep the + prefix and leave other fields unchanged. "
                  "Save the modal, then save the product. Reload the product, reopen Option, and read the same row's displayed Weight"
                + (" and Quantity" if "quantity" in values else "")
                + ". Report pass if the reopened values match the request, fail if they differ, or blocked if you cannot complete the check.")
            workflow = "target" if decimal else "preservation"
        fingerprint = digest({"case": case, "values": values, "start": entry["start"], "label": entry.get("label"), "field_order": list(values)})
        result.append({"id": entry["id"], "case_id": case, "build": entry["build"], "workflow": workflow,
            "family": entry["family"], "fingerprint": fingerprint, "data_split": entry["split"], "fixture": fixture,
            "public": {"task": task, "expected": expected}})
    if len({s["id"] for s in result}) != len(result) or len({s["fingerprint"] for s in result}) != len(result):
        raise ValueError("scenario IDs and semantic fingerprints must be unique")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--method", choices=["script", "sft"], default="sft")
    parser.add_argument("--app", choices=["impresscms", "opencart"])
    parser.add_argument("--only", action="append")
    parser.add_argument("--resume")
    parser.add_argument("--export", type=Path)
    args = parser.parse_args()
    register()
    catalog = ROOT / "configs/sft-impress-opencart-scenarios.yaml"
    config = load_config(ROOT / "configs/agent.yaml")
    if args.resume:
        directory = ROOT / "runs" / args.resume
        schedule = json.loads((directory / "schedule.json").read_text())
        chosen = set(schedule["scenario_ids"])
        work = [s for s in scenarios(directory / "catalog.yaml") if s["id"] in chosen]
        config, method = schedule["config"], schedule["method"]
        completed = json.loads((directory / "completed.json").read_text())
    else:
        work = [s for s in scenarios(catalog) if (not args.app or s["case_id"] == ("sweeper-084" if args.app == "impresscms" else "sweeper-125"))
                and (not args.only or s["id"] in args.only)]
        if not work:
            raise ValueError("no selected scenarios")
        directory = ROOT / "runs" / ("impress-opencart-" + uuid.uuid4().hex[:8])
        directory.mkdir()
        shutil.copy2(catalog, directory / "catalog.yaml")
        method, completed = args.method, []
        write_json(directory / "schedule.json", {"experiment": directory.name, "scenario_ids": [s["id"] for s in work],
            "config": config, "method": method})
        write_json(directory / "completed.json", completed)
    snapshot = directory / ("source-" + source_digest(ROOT) + ".tar.gz")
    if not snapshot.exists():
        with tarfile.open(snapshot, "w:gz") as archive:
            for path in (ROOT / "src/guiqa").rglob("*.py"):
                archive.add(path, arcname=str(path.relative_to(ROOT)))
            archive.add(Path(__file__), arcname="scripts/collect_impress_opencart.py")
    successful = {c["scenario_id"] for c in completed if c["matrix_matches"]}
    for scenario in work:
        if scenario["id"] in successful:
            continue
        path, run = run_case(ROOT, scenario["case_id"], scenario["build"], scenario["workflow"], method, config,
            port=13223 if scenario["case_id"] == "sweeper-084" else 13224, experiment=directory.name, scenario=scenario)
        expected = scenario["fixture"]["matrix_expected"]
        completed.append({"scenario_id": scenario["id"], "case_id": scenario["case_id"], "build": scenario["build"],
            "workflow": scenario["workflow"], "path": str(path.relative_to(ROOT)), "run_id": run["run_id"],
            "expected": expected, "oracle_verdict": run["oracle_verdict"], "agent_verdict": run["agent_verdict"],
            "matrix_matches": run["oracle_verdict"] == run["agent_verdict"] == expected})
        write_json(directory / "completed.json", completed)
        print(json.dumps(completed[-1], ensure_ascii=False), flush=True)
        if run["termination_reason"] == "interrupted":
            break
    successful = {c["scenario_id"] for c in completed if c["matrix_matches"]}
    matrix = {"planned": len(work), "successful_scenarios": len(successful), "attempts": len(completed),
              "passed": len(successful) == len(work), "pending": [s["id"] for s in work if s["id"] not in successful]}
    write_json(directory / "matrix.json", matrix)
    print(json.dumps({"experiment": directory.name, **matrix}, indent=2))
    if args.export and matrix["passed"]:
        manifest = export(ROOT, [directory.name], args.export, bundle_images=True)
        print(json.dumps({key: manifest[key] for key in ("apps", "trajectories", "samples")}, indent=2))
    return 0 if matrix["passed"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
