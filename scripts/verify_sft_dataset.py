"""Verify exported trajectories against their recorded executions and image bytes."""
import argparse
import collections
import hashlib
import json
from pathlib import Path

from PIL import Image

from guiqa.actions import parse_action
from guiqa.model import OPERATION_PROMPTS, REVIEW_PROMPTS
from guiqa.control import review_guard
from guiqa.sft_data import review_text, user_text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    report = verify_dataset(root, args.dataset.resolve())
    path = args.report or args.dataset / "verification.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def verify_dataset(root, directory):
    manifest = json.loads((directory / "manifest.json").read_text())
    assert checksum(directory / "train.jsonl") == manifest["data_sha256"]
    partitions = {"train": read_rows(directory / "train.jsonl")}
    if "validation_sha256" in manifest:
        assert checksum(directory / "validation.jsonl") == manifest["validation_sha256"]
        partitions["validation"] = read_rows(directory / "validation.jsonl")
    rows = [row for partition in partitions.values() for row in partition]
    assert len(rows) == manifest["samples"]
    assert len({row["id"] for row in rows}) == len(rows)
    assert not {row["case_id"] for row in rows} & set(manifest["eval_holdout"])
    by_run, image_hashes, duplicate_keys = collections.defaultdict(list), {}, collections.Counter()
    accepted_by_id = {run["run_id"]: run for run in manifest["accepted_runs"]}
    for partition, samples in partitions.items():
        for row in samples:
            run_id = row["id"].rsplit(":", 1)[0]
            by_run[run_id].append((partition, row))
            profile = accepted_by_id[run_id].get("review_profile", "persistence")
            expected_prompt = REVIEW_PROMPTS[profile] if row["phase"] == "review" else OPERATION_PROMPTS[profile]
            assert row["messages"][0]["content"][0]["text"] == expected_prompt
            assert row["messages"][-1]["role"] == "assistant"
            answer = json.loads(row["messages"][-1]["content"])
            images = []
            for message in row["messages"][:-1]:
                for item in message["content"]:
                    if item["type"] != "image":
                        continue
                    path = Path(item["image"])
                    if not path.is_absolute():
                        path = directory / path
                    if str(path) not in image_hashes:
                        with Image.open(path) as image:
                            assert image.size == (1280, 900)
                            image.verify()
                        image_hashes[str(path)] = checksum(path)
                    images.append(image_hashes[str(path)])
            key = json.dumps([row["messages"][1]["content"][0]["text"], images, answer], sort_keys=True, ensure_ascii=False)
            duplicate_keys[hashlib.sha256(key.encode()).hexdigest()] += 1
    assert set(by_run) == {run["run_id"] for run in manifest["accepted_runs"]}
    fingerprints = collections.defaultdict(set)
    labels = collections.Counter()
    for accepted in manifest["accepted_runs"]:
        run_id = accepted["run_id"]
        source_dir = root / "runs" / run_id
        result = json.loads((source_dir / "result.json").read_text())
        config = json.loads((source_dir / "manifest.json").read_text())["config"]
        source = source_dir / "sft/samples.jsonl"
        assert checksum(source) == accepted["source_sha256"]
        raw = read_rows(source)
        exported = by_run[run_id]
        assert len(exported) == len(raw) == accepted["samples"]
        split_names = {partition for partition, _ in exported}
        assert len(split_names) == 1
        partition = next(iter(split_names))
        assert partition == result.get("data_split", "train")
        if "scenario_fingerprint" in result:
            fingerprints[partition].add(result["scenario_fingerprint"])
        trace = read_rows(source_dir / "trace.jsonl")
        assert [step["step"] for step in trace] == list(range(result["actions"] + 1))
        history = [{"step": step["step"], "action": step["action"], "result": step["execution_error"] or "executed"}
                   for step in trace]
        for (_, row), original in zip(sorted(exported, key=lambda item: int(item[1]["id"].rsplit(":", 1)[1])), raw):
            answer = json.loads(row["messages"][-1]["content"])
            assert answer == original["assistant"]
            parse_action(json.dumps(answer), set(range(result["actions"] + 1)))
            step = original["step"]
            expected_text = (review_text(result["task"], history) if row["phase"] == "review" else
                             user_text(result["task"], history[:step + 1], config["history_actions"]))
            assert row["messages"][1]["content"][0]["text"] == expected_text
            original_images = [item["image"] for message in original["messages"] for item in message["content"]
                               if item["type"] == "image"]
            exported_images = [item["image"] for message in row["messages"][:-1] for item in message["content"]
                               if item["type"] == "image"]
            assert len(original_images) == len(exported_images)
            for before, after in zip(original_images, exported_images):
                target = Path(after) if Path(after).is_absolute() else directory / after
                assert checksum(Path(before)) == image_hashes[str(target)]
        review = raw[-1]
        assert review_guard(review["assistant"], result.get("review_profile", "persistence")) is None
        assert review["phase"] == "review" and review["label_source"] == "observed_run_v2"
        assert review["assistant"]["verdict"] == result["agent_verdict"] == result["oracle_verdict"]
        assert result["termination_reason"] == "sft_recorded" and result["model_calls"] == 0
        labels[result["agent_verdict"]] += 1
        assert sum(row["phase"] == "operation" for row in raw) == result["actions"]
        assert sum(row["phase"] == "review" for row in raw) == 1
    assert not fingerprints["train"] & fingerprints["validation"]
    if "media_manifest_sha256" in manifest:
        assert checksum(directory / "media_manifest.json") == manifest["media_manifest_sha256"]
        media = json.loads((directory / "media_manifest.json").read_text())
        assert len(media) == manifest["images"]
        for relative, item in media.items():
            assert checksum(directory / relative) == item["sha256"]
    return {"dataset": str(directory), "passed": True, "apps": manifest["apps"],
            "trajectories": len(by_run), "samples": len(rows),
            "partitions": {name: len(samples) for name, samples in partitions.items()},
            "review_labels": dict(labels), "unique_referenced_images": len(image_hashes),
            "duplicate_exact_samples": sum(count - 1 for count in duplicate_keys.values()),
            "complete_trajectories_preserved": True, "scenario_partition_overlap": 0,
            "prompts_and_action_histories_match": True, "labels_match_observed_runs": True,
            "image_bytes_match_recorded_screenshots": True, "source_sample_hashes_match": True,
            "holdout_excluded": True}


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


if __name__ == "__main__":
    main()
