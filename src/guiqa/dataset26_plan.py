"""Freeze the approved 26-application collection plan against pinned source bytes."""

import argparse
import hashlib
import json
from pathlib import Path

import yaml

from .dataset26_runtime import write_json


ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/dataset26-v1.yaml")
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text())
    source = ROOT / config["source"]
    rows = {row["case_id"]: row for row in map(json.loads, source.read_text().splitlines())}
    validate(config, rows)
    plan = make_plan(config, rows)
    plan.update(config_sha256=digest(args.config), source_sha256=digest(source))
    directory = ROOT / config["runs"]
    target = directory / "plan.json"
    if target.exists() and json.loads(target.read_text()) != plan:
        raise ValueError("the existing frozen collection plan differs")
    write_json(target, plan)
    prepare_cases(plan, rows)
    print(json.dumps({"plan": str(target), "applications": len(plan["applications"]),
                      "trajectories": len(plan["jobs"]), "e2e_executions": len(plan["e2e_jobs"])}))


def validate(config, rows):
    selected = []
    repositories = []
    for split, categories in config["applications"].items():
        expected = set("FVOLNXAB" if split == "test" else "FVOLNX")
        if set(categories) != expected:
            raise ValueError("operation categories differ from the approved plan")
        for category, cases in categories.items():
            if len(cases) != (2 if split == "train" else 1):
                raise ValueError("per-category application count differs")
            for case in cases:
                selected.append(case)
                repositories.append(rows[case]["repo"].lower().rstrip("/"))
    if len(set(selected)) != 26 or len(set(repositories)) != 26:
        raise ValueError("applications and repositories must be disjoint")


def make_plan(config, rows):
    applications, jobs, e2e = [], [], []
    for split, categories in config["applications"].items():
        quota = config["quotas"][split]["trajectories_per_app"]
        for category, cases in categories.items():
            for case_id in cases:
                case = rows[case_id]
                applications.append({"case_id": case_id, "slug": case["slug"], "repo": case["repo"],
                    "split": split, "category": category, "trajectories": quota,
                    "operation_samples": quota * 5, "review_samples": quota,
                    "base_commit": case["base_commit"], "reference_commit": case["reference_commit"]})
                for index in range(1, quota // 2 + 1):
                    instance = f"d26-{case_id}-{index:03d}"
                    for build, field in config["builds"].items():
                        jobs.append({"case_id": case_id, "slug": case["slug"], "split": split,
                            "category": category, "build": build, "instance_id": instance,
                            "index": index, "source_commit": case[field], "run_id": instance + "-" + build})
                if split == "test":
                    for build, field in config["builds"].items():
                        e2e.append({"case_id": case_id, "category": category, "build": build,
                            "instance_id": f"d26-e2e-{case_id}", "source_commit": case[field],
                            "reserved": True})
    return {"version": 1, "dataset": config["dataset"], "frozen_before_collection": True,
            "applications": applications, "jobs": jobs, "e2e_jobs": e2e,
            "targets": {"trajectories": 940, "review_samples": 940,
                        "operation_samples": 4700, "samples": 5640},
            "boundaries": config["boundaries"]}


def prepare_cases(plan, rows):
    for app in plan["applications"]:
        directory = ROOT / "cases" / app["case_id"]
        directory.mkdir(exist_ok=True)
        path = directory / "private.json"
        if not path.exists():
            write_json(path, rows[app["case_id"]])


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


if __name__ == "__main__":
    main()
