"""Dataset26 checks at the SFT data boundary."""

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re

from .actions import parse_action


def load_training_partitions(dataset, dataset_format=None):
    dataset = Path(dataset)
    partitions = {split: read_jsonl(dataset / (split + ".jsonl"))
                  for split in ("train", "validation")}
    summary = validate_dataset26(dataset, partitions) if dataset_format == "dataset26-v1" else None
    return partitions["train"], partitions["validation"], summary


def validate_dataset26(dataset, partitions):
    dataset = Path(dataset)
    manifest = json.loads((dataset / "manifest.json").read_text())
    plan = json.loads((dataset / "plan.json").read_text())
    if manifest["dataset"] != "dataset26-v1" or not manifest["complete"]:
        raise ValueError("dataset26 training requires a completed dataset26-v1 package")
    if manifest["review_format"] != "business_evidence_v1":
        raise ValueError("dataset26 training requires business_evidence_v1 reviews")
    if checksum(dataset / "plan.json") != manifest["plan_sha256"]:
        raise ValueError("dataset26 plan differs from its packaged manifest")
    applications = {app["case_id"]: app for app in plan["applications"]}
    if len(applications) != 26 or len({app["repo"] for app in applications.values()}) != 26:
        raise ValueError("dataset26 requires 26 distinct application repositories")
    counts = {}
    sample_ids = set()
    image_paths, upload_paths = set(), set()
    for split, rows in partitions.items():
        path = dataset / (split + ".jsonl")
        if checksum(path) != manifest["files"][path.name]["sha256"]:
            raise ValueError("dataset26 sample file differs from its manifest: " + path.name)
        expected_apps = {cid: app for cid, app in applications.items() if app["split"] == split}
        counts[split] = validate_partition(rows, split, expected_apps, sample_ids,
                                           image_paths, upload_paths)
    missing = [name for name in image_paths | upload_paths if not (dataset / name).is_file()]
    if missing:
        raise ValueError("dataset26 media is missing: " + missing[0])
    return {"dataset": "dataset26-v1", "partitions": counts,
            "images_referenced": len(image_paths), "upload_files_referenced": len(upload_paths),
            "test_used_for_training": False}


def validate_partition(rows, split, applications, sample_ids, image_paths, upload_paths):
    from .business_review import parse_review, review_guard

    per_app, phases = Counter(), Counter()
    runs = defaultdict(list)
    for row in rows:
        if row["id"] in sample_ids:
            raise ValueError("dataset26 sample ID occurs more than once: " + row["id"])
        sample_ids.add(row["id"])
        if row["case_id"] not in applications or row["split"] != split:
            raise ValueError("dataset26 sample crosses its application split: " + row["id"])
        app = applications[row["case_id"]]
        if row["category"] != app["category"] or (split != "test" and row["category"] in {"A", "B"}):
            raise ValueError("dataset26 sample uses the wrong operation category: " + row["id"])
        if row["build"] not in {"A", "B"}:
            raise ValueError("dataset26 sample build must be A or B")
        commit_key = "base_commit" if row["build"] == "A" else "reference_commit"
        if row["source_commit"] != app[commit_key]:
            raise ValueError("dataset26 sample source differs from its frozen build: " + row["id"])
        messages = row["messages"]
        if messages[-1]["role"] != "assistant":
            raise ValueError("SFT requires the final assistant target: " + row["id"])
        for message in messages[:-1]:
            image_paths.update(item["image"] for item in message["content"] if item["type"] == "image")
        raw = messages[-1]["content"]
        if row["phase"] == "operation":
            action = parse_action(raw, {row["step"]})
            if action["action"] == "finish":
                raise ValueError("operation sample cannot contain a review target")
            if action["action"] in {"select", "upload"}:
                validate_native_control(row, action)
            if action["action"] == "upload":
                upload_paths.add(action["file"])
        elif row["phase"] == "review":
            answer = parse_review(raw, screenshot_steps(row), requirements=row["requirements"])
            violation = review_guard(answer, requirements=row["requirements"])
            if violation:
                raise ValueError(row["id"] + ": " + violation)
        else:
            raise ValueError("dataset26 phase must be operation or review")
        per_app[row["case_id"]] += 1
        phases[row["phase"]] += 1
        runs[(row["instance_id"], row["build"])].append(row)
    if set(per_app) != set(applications):
        raise ValueError("dataset26 partition does not contain its planned applications: " + split)
    for cid, app in applications.items():
        if per_app[cid] != app["operation_samples"] + app["review_samples"]:
            raise ValueError("dataset26 sample quota differs for " + cid)
    instances = defaultdict(set)
    for (instance, build), group in runs.items():
        operations = [row for row in group if row["phase"] == "operation"]
        if len(group) != 6 or len(operations) != 5 or len({row["target_step"] for row in operations}) != 5:
            raise ValueError("dataset26 trajectory needs five distinct operations and one review: " + instance)
        instances[instance].add(build)
    if any(builds != {"A", "B"} for builds in instances.values()):
        raise ValueError("dataset26 trajectories must contain both builds of every instance")
    return {"applications": len(per_app), "trajectories": len(runs), "samples": len(rows),
            "phases": dict(phases), "case_ids": sorted(per_app)}


def screenshot_steps(row):
    return {int(match.group(1)) for message in row["messages"][:-1]
            for item in message["content"] if item["type"] == "text"
            for match in re.finditer(r"Screenshot step (\d+)", item["text"])}


def validate_native_control(row, action):
    text = "\n".join(item["text"] for message in row["messages"][:-1]
                     for item in message["content"] if item["type"] == "text")
    marker = "Native control tools:\n"
    if marker not in text:
        raise ValueError("native control target is absent from the public input: " + row["id"])
    declared, _ = json.JSONDecoder().raw_decode(text.split(marker, 1)[1].lstrip())
    if declared != row["native_controls"] or not any(
        tool["action"] == action["action"] and tool["selector"] == action["selector"]
        for tool in declared
    ):
        raise ValueError("native control target differs from its public tool catalog: " + row["id"])


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def checksum(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
