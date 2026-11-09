"""Package real review trajectories into separate expanded training and test datasets."""

import copy
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

import yaml

from .review_contract import parse_review, review_guard
from .review_facts import facts_prompt, to_facts
from .sft_v2 import absolute_images, bundle_images, checksum, read_json, read_rows, write_json, write_rows


def build_packages(root, batches, output, test_output, rl_output):
    root = Path(root).resolve()
    plan = read_json(root / "runs/review-expansion-v1/plan.json")
    split = yaml.safe_load((root / "configs/sft-split.yaml").read_text())
    original, legacy, gold = load_original(root)
    added, sources = [], []
    for directory in batches:
        rows, answers, source = load_batch(root, Path(directory).resolve())
        added.extend(rows)
        gold.update(answers)
        sources.append(source)
    counts = dict(Counter(row["data_split"] for row in added))
    if counts != plan["new_review_targets"]:
        raise ValueError(f"collected review counts differ from the plan: {counts}")
    validate_partitions(original + legacy + added, gold, split)
    training = original + [row for row in added if row["data_split"] == "train"]
    development = [row for row in added if row["data_split"] == "validation"]
    testing = [row for row in added if row["data_split"] == "test"]
    replay = [row for row in read_rows(root / "datasets/sft-review-v5/train.jsonl")
              if row["phase"] == "operation"]
    for row in replay:
        absolute_images(row, root / "datasets/sft-review-v5")
    result = {
        "training": write_package(Path(output), {"train": training, "validation": development,
                                                 "legacy-validation": legacy}, gold, replay,
                                  [source for source in sources if set(source["data_splits"]) & {"train", "validation"}]),
        "final_test": write_package(Path(test_output), {"test": testing}, gold, [],
                                    [source for source in sources if "test" in source["data_splits"]]),
    }
    result["rl_reviews"] = write_rl_package(Path(output), Path(rl_output))
    test_manifest_path = Path(test_output) / "manifest.json"
    test_manifest = read_json(test_manifest_path)
    for name in ("final-test-source-state.json", "final-test-calibration-verification.json"):
        destination = Path(test_output) / name
        shutil.copyfile(root / "runs/review-expansion-v1" / name, destination)
        test_manifest[name] = {"file": name, "sha256": checksum(destination)}
    write_json(test_manifest_path, test_manifest)
    result["final_test"]["manifest_sha256"] = checksum(test_manifest_path)
    changed = [name for name, digest in plan["source_sha256"].items() if checksum(root / name) != digest]
    if changed:
        raise ValueError("preserved source changed: " + ", ".join(changed))
    write_json(root / "runs/review-expansion-v1/completion.json", {
        "completed": True, "new_review_counts": counts, "source_bytes_preserved": True,
        "training_started": False, "packages": result})
    return result


def load_original(root):
    directory = root / "datasets/rl-review-pilot-v2"
    manifest, media = read_json(directory / "manifest.json"), read_json(directory / "media_manifest.json")
    for name, key in (("train.jsonl", "data_sha256"), ("validation.jsonl", "validation_sha256"),
                      ("media_manifest.json", "media_manifest_sha256")):
        if checksum(directory / name) != manifest[key]:
            raise ValueError("original dataset differs from its manifest: " + name)
    training, legacy, gold = read_rows(directory / "train.jsonl"), read_rows(directory / "validation.jsonl"), {}
    for row in training + legacy:
        gold[row["id"]] = parse_gold(row)
        for item in images(row):
            if checksum(directory / item["image"]) != media[item["image"]]["sha256"]:
                raise ValueError("original screenshot changed: " + item["image"])
        absolute_images(row, directory)
    for row in legacy:
        row["data_split"] = "legacy-validation"
    return training, legacy, gold


def load_batch(root, directory):
    review_file = directory if directory.suffix == ".jsonl" else directory / "reviews.jsonl"
    directory = review_file.parent
    manifest = read_json(directory / "manifest.json")
    if not manifest["complete"] or manifest["calibration"]:
        raise ValueError("packaging requires a complete non-calibration collection: " + str(directory))
    for name, digest in manifest["dataset_sha256"].items():
        if checksum(directory / name) != digest:
            raise ValueError("collection file changed: " + str(directory / name))
    derivation = None
    if review_file.name != "reviews.jsonl":
        derivation_path = directory / "review-task-derivations.json"
        derivation = read_json(derivation_path)
        if (derivation["source_sha256"] != manifest["dataset_sha256"]["reviews.jsonl"]
                or checksum(review_file) != derivation["output_sha256"]):
            raise ValueError("derived review tasks differ from their recorded source or output")
    completed = {row["run_id"]: row for row in manifest["completed"]}
    rows, gold = read_rows(review_file), {}
    for row in rows:
        answer = parse_gold(row)
        run = Path(row["run_directory"])
        provenance = row["derived_provenance"]
        result = read_json(run / "result.json")
        if (not result["complete"] or row["calibration"]
                or completed[provenance["source_run_id"]] != result):
            raise ValueError("collection row differs from its completed real run: " + row["id"])
        for name, digest in result["source_hashes"].items():
            if checksum(run / name) != digest:
                raise ValueError("trajectory artifact changed: " + str(run / name))
        deployment = read_json(run / "private/instance/instance.json")
        case = read_json(root / "cases" / row["case_id"] / "private.json")
        expected_commit = case["base_commit" if row["build"] == "A" else "reference_commit"]
        if deployment["case_id"] != row["case_id"] or deployment["commit"] != expected_commit:
            raise ValueError("trajectory deployment differs from the pinned application version")
        trace = {entry["step"]: entry for entry in read_rows(run / "trace.jsonl")}
        steps = [frame["step"] for frame in row["review_frames"]]
        source_images = images(row)
        if len(source_images) != len(steps):
            raise ValueError("review screenshots do not align with their recorded steps")
        for step, item in zip(steps, source_images):
            name = f"screenshots/{step:03d}.png"
            if (trace[step]["screenshot"] != name
                    or checksum(directory / item["image"]) != result["source_hashes"][name]):
                raise ValueError("review screenshot differs from its real trace step")
        if answer != read_json(run / "label.json") or result["verdict"] != answer["verdict"]:
            raise ValueError("review answer differs from the real run's label")
        if provenance["source_trace_sha256"] != result["source_hashes"]["trace.jsonl"]:
            raise ValueError("review trace provenance differs from its source")
        gold[row["id"]] = answer
        row["derived_provenance"]["collection_manifest"] = str(directory / "manifest.json")
        absolute_images(row, directory)
    return rows, gold, {"path": str(directory), "manifest_sha256": checksum(directory / "manifest.json"),
                        "reviews_file": str(review_file), "reviews_sha256": checksum(review_file),
                        "review_samples": len(rows), "actual_runs": len(completed),
                        "data_splits": dict(Counter(row["data_split"] for row in rows)),
                        "task_derivations": derivation}


def parse_gold(row):
    steps = {frame["step"] for frame in row["review_frames"]}
    answer = parse_review(row["messages"][-1]["content"], steps, row["review_profile"])
    violation = review_guard(answer, row["review_profile"])
    if violation:
        raise ValueError("gold contradicts the visible review contract: " + violation)
    return answer


def review_keys(row, answer):
    text = row["messages"][1]["content"][0]["text"]
    task, marker, _ = text.partition("\nSupplied screenshot steps:")
    if not marker:
        raise ValueError("review input has no public task/screenshot boundary")
    common = [row["case_id"], row["review_profile"]]
    requirements = sorted((field["field"].strip().casefold(), field["intent"], field["requested_value"])
                          for field in answer["field_readings"])
    return {"public_task": content_hash([*common, task]),
            "requirements": content_hash([*common, answer["task_kind"], requirements])}


def validate_partitions(rows, gold, split):
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("review IDs must be unique across all partitions")
    training_cases = set(split["training_pilot"] + split["training_supplementary"])
    final_cases = set(split["eval_holdout"])
    locations = {name: {} for name in ("public_task", "requirements", "parent", "source_run")}
    for row in rows:
        partition = row["data_split"]
        allowed = final_cases if partition == "test" else training_cases
        if row["case_id"] not in allowed:
            raise ValueError("application is outside its assigned train/test partition")
        keys = review_keys(row, gold[row["id"]])
        keys.update(parent=row.get("parent_scenario_id", keys["public_task"]),
                    source_run=row["derived_provenance"]["source_run_id"])
        for name, key in keys.items():
            previous = locations[name].setdefault(key, partition)
            if previous != partition:
                raise ValueError(f"{name} crosses {previous}/{partition}: {row['id']}")


def write_package(output, partitions, gold, replay, sources):
    output.mkdir(parents=True, exist_ok=False)
    full = {name: copy.deepcopy(rows) for name, rows in partitions.items()}
    facts = {}
    for name, rows in full.items():
        facts[name] = []
        for original in rows:
            row = copy.deepcopy(original)
            row["review_format"] = "evidence_facts_v1"
            row["messages"][0]["content"] = [{"type": "text", "text": facts_prompt(row["review_profile"])}]
            row["messages"][-1]["content"] = json.dumps(to_facts(gold[row["id"]]), ensure_ascii=False)
            facts[name].append(row)
    samples = [row for rows in [*full.values(), *facts.values()] for row in rows] + replay
    media = bundle_images(samples, output)
    counts, labels, families, hashes = {}, {}, {}, {}
    for name in partitions:
        reviews = facts[name]
        rows = reviews + replay if name == "train" else reviews
        files = {f"{name}.jsonl": rows, f"{name}-reviews.jsonl": reviews,
                 f"{name}-evidence-v2.jsonl": full[name]}
        for filename, records in files.items():
            write_rows(output / filename, records)
            hashes[filename] = checksum(output / filename)
        counts[name] = {"reviews": len(reviews), "samples": len(rows)}
        labels[name] = dict(Counter(gold[row["id"]]["verdict"] for row in reviews))
        families[name] = len({row.get("parent_scenario_id", review_keys(row, gold[row["id"]])["public_task"])
                              for row in full[name]})
    write_json(output / "media_manifest.json", media)
    manifest = {"version": 1, "stage": "expanded_review_data", "review_format": "evidence_facts_v1",
                "counts": counts, "review_labels_by_split": labels, "parent_scenario_counts": families,
                "images": len(media), "collection_sources": sources, "data_sha256_by_file": hashes,
                "media_manifest_sha256": checksum(output / "media_manifest.json"),
                "training_started": False, "original_artifacts_preserved": True,
                "teacher_model_calls": 0, "human_pixel_review_claimed": False}
    write_json(output / "manifest.json", manifest)
    write_json(output / "verification.json", {"passed": True, "counts": counts,
        "content_and_parent_partitions_disjoint": True, "final_test_applications_isolated": True,
        "same_run_trace_images_gold_and_deployment_verified": True})
    return {"directory": str(output), "manifest_sha256": checksum(output / "manifest.json"),
            "counts": counts, "labels": labels, "parent_scenarios": families}


def images(row):
    return [item for message in row["messages"][:-1] for item in message["content"] if item["type"] == "image"]


def write_rl_package(source, output):
    output.mkdir(parents=True, exist_ok=False)
    partitions = {name: read_rows(source / f"{name}-evidence-v2.jsonl") for name in ("train", "validation")}
    rows = [row for samples in partitions.values() for row in samples]
    for row in rows:
        absolute_images(row, source)
    media = bundle_images(rows, output)
    for name, samples in partitions.items():
        write_rows(output / f"{name}.jsonl", samples)
    write_json(output / "media_manifest.json", media)
    manifest = {"version": 1, "stage": "expanded_review_rl_data", "review_format": "evidence_v2",
                "samples": len(rows), "review_samples": len(rows), "operation_samples": 0,
                "train_samples": len(partitions["train"]), "validation_samples": len(partitions["validation"]),
                "source_package": str(source), "source_manifest_sha256": checksum(source / "manifest.json"),
                "data_sha256": checksum(output / "train.jsonl"),
                "validation_sha256": checksum(output / "validation.jsonl"),
                "media_manifest_sha256": checksum(output / "media_manifest.json"),
                "training_started": False, "images": len(media)}
    write_json(output / "manifest.json", manifest)
    return {"directory": str(output), "manifest_sha256": checksum(output / "manifest.json"),
            "train_reviews": len(partitions["train"]), "development_reviews": len(partitions["validation"])}


def content_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
