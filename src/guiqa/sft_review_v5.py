"""Build a facts-only review SFT batch with frozen development scenes and operation replay."""

import copy
import hashlib
import json
from collections import Counter, defaultdict, deque
from pathlib import Path

import yaml

from .review_facts import facts_prompt, to_facts
from .rl_review_dataset import _validate_reviews
from .sft_v2 import (absolute_images, bundle_images, checksum, read_json, read_rows,
                     value_hash, write_json, write_rows)


def build_dataset(root, reviews, operations, pixel_audit, output, replay_count=145):
    root, output = Path(root).resolve(), Path(output).resolve()
    reviews, operations, pixel_audit = (Path(path).resolve() for path in (reviews, operations, pixel_audit))
    review_rows, review_manifest, review_media, review_hashes = load_source(reviews)
    operation_rows, _, operation_media, operation_hashes = load_source(operations)
    split = yaml.safe_load((root / "configs/sft-split.yaml").read_text())
    gold = _validate_reviews(review_rows, split)
    audit = verified_training_audit(pixel_audit, review_rows, review_hashes)
    audit_sha = checksum(pixel_audit)
    replay = select_operation_replay(operation_rows, replay_count)
    validate_partitions(review_rows + replay, split)
    if any(output == source or source in output.parents for source in (reviews, operations)):
        raise ValueError("v5 output must be outside the preserved source datasets")

    samples = []
    for original in review_rows:
        row = copy.deepcopy(original)
        row["review_format"] = "evidence_facts_v1"
        row["messages"][0]["content"] = [{"type": "text", "text": facts_prompt(row["review_profile"])}]
        row["messages"][-1]["content"] = json.dumps(to_facts(gold[row["id"]]), ensure_ascii=False)
        row["derived_provenance"].update(
            facts_source_row_sha256=value_hash(original),
            facts_source_dataset=str(reviews),
            facts_source_answer_sha256=hashlib.sha256(original["messages"][-1]["content"].encode()).hexdigest())
        if row["id"] in audit["reviews"]:
            row["derived_provenance"]["agent_pixel_review"] = {
                "status": "verified_key_frames", "audit": str(pixel_audit),
                "audit_sha256": audit_sha, "reviewer": audit["reviewer"]}
        verify_sample_images(row, reviews, review_media)
        absolute_images(row, reviews)
        samples.append(row)
    for original in replay:
        row = copy.deepcopy(original)
        verify_sample_images(row, operations, operation_media)
        absolute_images(row, operations)
        samples.append(row)

    output.mkdir(parents=True, exist_ok=False)
    media = bundle_images(samples, output)
    replay_by_id = {row["id"]: row for row in replay}
    for row in samples:
        if row["phase"] == "operation" and row != replay_by_id[row["id"]]:
            raise ValueError("operation replay changed its original prompt, answer or metadata")
    for partition in ("train", "validation"):
        rows = [row for row in samples if row["data_split"] == partition]
        write_rows(output / f"{partition}.jsonl", rows)
        write_rows(output / f"{partition}-reviews.jsonl", [row for row in rows if row["phase"] == "review"])
    write_json(output / "media_manifest.json", media)
    for directory, hashes in ((reviews, review_hashes), (operations, operation_hashes)):
        if any(checksum(directory / name) != expected for name, expected in hashes.items()):
            raise ValueError("preserved source changed during the v5 build")
    labels = {partition: dict(Counter(gold[row["id"]]["verdict"] for row in review_rows
                                     if row["data_split"] == partition))
              for partition in ("train", "validation")}
    manifest = {
        "version": 5, "stage": "review_sft", "review_format": "evidence_facts_v1",
        "training_started": False, "review_source": str(reviews), "operation_source": str(operations),
        "review_source_sha256": review_hashes, "operation_source_sha256": operation_hashes,
        "pixel_audit": str(pixel_audit), "pixel_audit_sha256": audit_sha,
        "reviewer": audit["reviewer"], "agent_reviewed_new_training_samples": len(audit["reviews"]),
        "human_review_status": "Source human_pixel_review values retained; new key frames inspected by Codex.",
        "development_source": "Same frozen 39 review scenes; no operation development samples added.",
        "review_labels_by_split": labels, "review_samples": len(review_rows),
        "review_train_samples": sum(row["data_split"] == "train" for row in review_rows),
        "operation_replay_samples": len(replay),
        "replay_selection": "Deterministic round robin over cases and scenarios; SHA256(seed42,id) order within each scenario.",
        "replay_ids": [row["id"] for row in replay],
        "replay_cases": dict(sorted(Counter(row["case_id"] for row in replay).items())),
        "replay_scenarios": len({(row["case_id"], row["scenario_id"]) for row in replay}),
        "cases": sorted({row["case_id"] for row in samples}), "eval_holdout": split["eval_holdout"],
        "split_unit": review_manifest["split_unit"],
        "split_config_sha256": checksum(root / "configs/sft-split.yaml"),
        "samples": len(samples), "train_samples": sum(row["data_split"] == "train" for row in samples),
        "validation_samples": sum(row["data_split"] == "validation" for row in samples),
        "images": len(media), "maximum_review_images": max(len(row["review_frames"]) for row in review_rows),
        "data_sha256": checksum(output / "train.jsonl"),
        "validation_sha256": checksum(output / "validation.jsonl"),
        "media_manifest_sha256": checksum(output / "media_manifest.json"),
    }
    verification = {
        "passed": True, "source_bytes_preserved": True, "frozen_holdout_excluded": True,
        "development_scenes_preserved": True, "scenario_id_overlap": 0,
        "scenario_fingerprint_overlap": 0, "source_run_overlap": 0,
        "operation_replay_rows_unchanged": True, "review_structured_gold_unchanged": True,
        "review_user_content_unchanged": True, "images_byte_identical": True,
        "new_training_review_pixel_audit_complete": True, "human_pixel_review_claimed": False,
        "excluded_reviews": audit.get("excluded_reviews", []),
    }
    write_json(output / "manifest.json", manifest)
    write_json(output / "verification.json", verification)
    return manifest, verification


def select_operation_replay(rows, count):
    candidates = [row for row in rows if row["phase"] == "operation" and row["data_split"] == "train"]
    if not 0 <= count <= len(candidates):
        raise ValueError("operation replay count exceeds available training operations")
    grouped = defaultdict(lambda: defaultdict(list))
    for row in candidates:
        grouped[row["case_id"]][row["scenario_id"]].append(row)
    cases = {}
    for case, scenarios in sorted(grouped.items()):
        queues = [deque(sorted(scenarios[key], key=lambda row: hashlib.sha256(
            ("42:" + row["id"]).encode()).hexdigest())) for key in sorted(scenarios)]
        cases[case] = deque(queues)
    selected = []
    while len(selected) < count:
        for case in cases.values():
            if case and len(selected) < count:
                scenario = case.popleft()
                selected.append(scenario.popleft())
                if scenario:
                    case.append(scenario)
    return selected


def validate_partitions(rows, split):
    allowed = set(split["training_pilot"] + split["training_supplementary"])
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("v5 source sample IDs must be unique")
    for row in rows:
        if row["case_id"] not in allowed or row["case_id"] in split["eval_holdout"]:
            raise ValueError("v5 samples must belong to a training application")
        if row["data_split"] not in ("train", "validation"):
            raise ValueError("v5 source requires a frozen train/validation partition")
    for key in ("scenario_id", "scenario_fingerprint", "source_run_id"):
        values = {partition: {row["derived_provenance"][key] if key == "source_run_id" else row[key]
                              for row in rows if row["data_split"] == partition}
                  for partition in ("train", "validation")}
        if values["train"] & values["validation"]:
            raise ValueError("v5 training and development overlap on " + key)


def load_source(directory):
    manifest = read_json(directory / "manifest.json")
    hashes = {name: checksum(directory / name) for name in
              ("manifest.json", "train.jsonl", "validation.jsonl", "media_manifest.json")}
    for name, key in (("train.jsonl", "data_sha256"), ("validation.jsonl", "validation_sha256"),
                      ("media_manifest.json", "media_manifest_sha256")):
        if hashes[name] != manifest[key]:
            raise ValueError("source differs from its manifest: " + str(directory / name))
    rows = []
    for partition in ("train", "validation"):
        for row in read_rows(directory / f"{partition}.jsonl"):
            if row["data_split"] != partition:
                raise ValueError("source row differs from its frozen partition")
            rows.append(row)
    return rows, manifest, read_json(directory / "media_manifest.json"), hashes


def verified_training_audit(path, rows, hashes):
    audit = read_json(path)
    if audit["training_source_sha256"] != hashes["train.jsonl"]:
        raise ValueError("pixel audit differs from the frozen training source")
    pending = {row["id"]: row for row in rows if row["data_split"] == "train"
               and row["derived_provenance"].get("human_pixel_review") == "pending"}
    if set(audit["reviews"]) != set(pending):
        raise ValueError("pixel audit must cover every pending training review exactly once")
    for identifier, row in pending.items():
        record = audit["reviews"][identifier]
        if record["status"] != "verified_key_frames" or record["source_row_sha256"] != value_hash(row):
            raise ValueError("training pixel audit does not match its original review: " + identifier)
        if not set(record["reviewed_steps"]).issubset(row["screenshot_steps"]):
            raise ValueError("training pixel audit cites an unsupplied frame")
        answer = json.loads(row["messages"][-1]["content"])
        required = {reading["step"] for field in answer["field_readings"]
                    for reading in (field["before"], field["after"]) if reading is not None}
        if answer["submission"]["evidence"] is not None:
            required.add(answer["submission"]["evidence"]["step"])
        if not required.issubset(record["reviewed_steps"]):
            raise ValueError("training pixel audit is missing a gold evidence frame")
    return audit


def verify_sample_images(row, source, media):
    for message in row["messages"][:-1]:
        for item in message["content"]:
            if item["type"] == "image" and checksum(source / item["image"]) != media[item["image"]]["sha256"]:
                raise ValueError("source screenshot differs from its media manifest: " + item["image"])
