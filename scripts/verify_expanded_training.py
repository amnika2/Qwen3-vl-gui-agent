"""Check the collected labels, source artifacts and registered partitions."""

import argparse
import json
from collections import Counter
from pathlib import Path

import yaml

from guiqa.expanded_training_collection import LABELS, file_hash, prepare_task, scenario_identity
from guiqa.review_contract import parse_review, review_guard
from guiqa.rl_review_dataset import _verify_curriculum_run


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reviews", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows, batches = [], []
    for path in args.reviews:
        part, record = verify_batch(path.resolve())
        rows.extend(part)
        batches.append(record)
    for key in ("id",):
        assert len({row[key] for row in rows}) == len(rows)
    assert len({row["derived_provenance"]["source_run_id"] for row in rows}) == len(rows)
    counts = Counter(row["data_split"] for row in rows)
    assert counts == {"train": 160, "validation": 18}
    for key in ("parent_scenario_id", "content_key"):
        assert not {row[key] for row in rows if row["data_split"] == "train"} & {
            row[key] for row in rows if row["data_split"] == "validation"}
    split = yaml.safe_load((ROOT / "configs/sft-split.yaml").read_text())
    assert not {row["case_id"] for row in rows} & set(split["eval_holdout"])
    result = {"passed": True, "reviews": len(rows), "split_counts": dict(counts),
              "parent_scenarios": len({row["parent_scenario_id"] for row in rows}),
              "parents_by_split": {part: len({row["parent_scenario_id"] for row in rows if row["data_split"] == part})
                                   for part in ("train", "validation")},
              "case_split_counts": dict(Counter(row["case_id"] + ":" + row["data_split"] for row in rows)),
              "label_counts": dict(Counter(json.loads(row["messages"][-1]["content"])["verdict"] for row in rows)),
              "label_counts_by_split": {part: dict(Counter(json.loads(row["messages"][-1]["content"])["verdict"]
                                                          for row in rows if row["data_split"] == part))
                                        for part in ("train", "validation")},
              "maximum_images": max(len(row["review_frames"]) for row in rows),
              "source_label_images_trace_deployment_verified": True,
              "visible_readings_match_cited_same_run_snapshots": True,
              "public_tasks_and_content_keys_match_source_generator": True,
              "original_raw_reviews_preserved": True, "holdout_excluded": True,
              "partition_overlap": 0, "training_started": False, "batches": batches}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def verify_batch(path):
    batch = path.parent
    manifest = json.loads((batch / "manifest.json").read_text())
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert manifest["complete"] and not manifest["calibration"]
    assert len(rows) == manifest["samples_planned"] == len(manifest["completed"])
    for name, saved in manifest["dataset_sha256"].items():
        assert file_hash(batch / name) == saved
    completed = {record["run_id"]: record for record in manifest["completed"]}
    assert {row["derived_provenance"]["source_run_id"] for row in rows} == set(completed)
    for row in rows:
        record = completed[row["derived_provenance"]["source_run_id"]]
        _verify_curriculum_run(ROOT, row, record, batch)
        directory = Path(row["run_directory"])
        private = json.loads((directory / "private/scenario.json").read_text())
        fixture, task, initial, requested = prepare_task(private["group"])
        metadata, _ = scenario_identity(private["group"], fixture, task, initial, requested)
        assert all(row[key] == value for key, value in metadata.items())
        assert row["messages"][1]["content"][0]["text"].split("\nSupplied screenshot steps:")[0] == task
        steps = {frame["step"] for frame in row["review_frames"]}
        assert len(steps) <= 5
        answer = parse_review(row["messages"][-1]["content"], steps, row["review_profile"])
        assert review_guard(answer, row["review_profile"]) is None
        assert answer["verdict"] == record["oracle_verdict"]
        expected = {LABELS.get(key, key.title()): value for key, value in requested.items()}
        assert {field["field"]: field["requested_value"] for field in answer["field_readings"]} == expected
        visible = json.loads((directory / "private/visible.json").read_text())
        for field in answer["field_readings"]:
            for phase in ("before", "after"):
                reading = field[phase]
                if reading is not None:
                    key = field["field"]
                    if row["case_id"] == "sweeper-105" and phase == "before" and key in {"Saved country rows", "Saved city rows"}:
                        key += " input"
                    assert visible[str(reading["step"])]["fields"][key]["value"] == reading["value"]
    derivations = json.loads((batch / "review-task-derivations.json").read_text())
    assert derivations["source_sha256"] == file_hash(batch / "reviews.jsonl")
    assert derivations["output_sha256"] == file_hash(path)
    return rows, {"path": str(path), "sha256": file_hash(path),
                  "original_reviews_sha256": derivations["source_sha256"],
                  "task_derivations": len(derivations["task_derivations"]), "samples": len(rows)}


if __name__ == "__main__":
    main()
