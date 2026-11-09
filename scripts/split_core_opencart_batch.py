"""Preserve collected OpenCart rows while splitting remaining parent groups over two workers."""

from argparse import Namespace
import copy
import json
from pathlib import Path
import shutil

from guiqa.rl_targeted_collection import collection_jobs, load_catalog, shard_jobs, snapshot_source
from guiqa.sft_v2 import checksum, read_json, read_rows, write_json, write_rows


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runs/review-expansion-v1"


def main():
    source = RUNTIME / "core-opencart-main"
    original = read_json(source / "manifest.json")
    catalog = load_catalog(source / "catalog.yaml")
    jobs = collection_jobs(catalog, Namespace(app=None, calibration=False, include_blocked=True,
                                             input_not_established_only=False))
    rows = {row["id"]: row for row in read_rows(source / "reviews.jsonl")}
    plan = read_json(RUNTIME / "core-plan.json")
    plan["batches"] = [batch for batch in plan["batches"] if batch["name"] != "core-opencart-main"]
    for index, port in ((0, 13434), (1, 13435)):
        selected_jobs = shard_jobs(jobs, index, 2)
        job_ids = {f"{group['app']}-{group['id']}-{build}-{variant}" for group, build, variant in selected_jobs}
        completed = [record for record in original["completed"] if record["job_id"] in job_ids]
        selected_rows = [copy.deepcopy(rows[record["run_id"]]) for record in completed]
        for row in selected_rows:
            for message in row["messages"][:-1]:
                for item in message["content"]:
                    if item["type"] == "image":
                        item["image"] = str((source / item["image"]).resolve())
        directory = RUNTIME / f"core-opencart-shard-{index}"
        directory.mkdir()
        shutil.copy2(source / "catalog.yaml", directory / "catalog.yaml")
        current_source = snapshot_source(directory)
        for partition in ("train", "validation"):
            write_rows(directory / f"{partition}.jsonl", [row for row in selected_rows if row["data_split"] == partition])
        for filename in ("reviews.jsonl", "all-reviews.jsonl"):
            write_rows(directory / filename, selected_rows)
        manifest = {
            "calibration": False, "runs_planned": len(selected_jobs), "samples_planned": len(selected_jobs),
            "training_applications_only": True, "teacher_model_calls": 0,
            "catalog_sha256": checksum(directory / "catalog.yaml"), "jobs": selected_jobs,
            "completed": completed, "retained_review_ids": [row["id"] for row in selected_rows],
            "failures": [], "complete": len(completed) == len(selected_jobs),
            "completed_runs": len(completed), "review_samples": len(selected_rows),
            "shards": 2, "shard_index": index, "shard_unit": "parent_scenario_id",
            "inherited_source_batch": str(source), "inherited_manifest_sha256": checksum(source / "manifest.json"),
            "collector_source_snapshots": [current_source],
            "dataset_sha256": {name: checksum(directory / name) for name in
                               ("reviews.jsonl", "all-reviews.jsonl", "train.jsonl", "validation.jsonl")},
        }
        write_json(directory / "manifest.json", manifest)
        plan["batches"].append({"name": directory.name, "catalog": "core-opencart-main.yaml", "port": port,
                                "flags": ["--include-blocked", "--shards", "2", "--shard-index", str(index)],
                                "raw_runs": len(selected_jobs),
                                "train_reviews": sum(group["split"] == "train" for group, _, _ in selected_jobs),
                                "dev_reviews": sum(group["split"] == "validation" for group, _, _ in selected_jobs)})
        print(json.dumps({"batch": str(directory), "already_collected": len(completed),
                          "remaining": len(selected_jobs) - len(completed)}))
    plan.setdefault("diagnostic_incomplete_batches", []).append("core-opencart-main")
    write_json(RUNTIME / "core-plan.json", plan)


if __name__ == "__main__":
    main()
