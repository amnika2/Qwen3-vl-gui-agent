"""Evaluate the 25 validation reviews in three shards, then merge saved predictions."""

import argparse
import copy
import hashlib
import json
from pathlib import Path

from guiqa.model import Qwen
from validate_review_protocol import (evaluate_row, file_hash, load_inputs, metrics,
                                      prepare_output, write_summary)


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = "scripts/validate_review_checkpoint_shards.py"


def main():
    args = arguments()
    if args.merge is not None:
        parent = args.merge.resolve()
        manifest, predictions, model_metrics = load_merge(parent)
        output = parent / "combined"
        output.mkdir()
        write_json(output / "manifest.json", manifest)
        (output / "predictions.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in predictions))
        summary = write_summary(output, predictions, manifest["samples"], model_metrics)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return
    rows, config, manifest = load_inputs(args)
    rows, manifest = shard_inputs(rows, manifest, args.shard_index, args.shards)
    output = args.output.resolve()
    predictions = prepare_output(output, manifest, resume=False)
    model = Qwen(config)
    model.load()
    for row in rows:
        result = evaluate_row(model, row, args.dataset.resolve(), manifest["adapter_sha256"])
        with (output / "predictions.jsonl").open("a") as handle:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
        predictions.append(result)
        write_summary(output, predictions, len(rows), model.metrics())
        print(json.dumps({"shard": args.shard_index, "completed": len(predictions), "total": len(rows),
                          "id": result["id"], "label": result["label"], "prediction": result["prediction"],
                          "parse_error": result["parse_error"], "guard": result["guard"]}), flush=True)
    summary = write_summary(output, predictions, len(rows), model.metrics())
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard-index", type=int, choices=range(3))
    parser.add_argument("--shards", type=int, choices=(3,), default=3)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--partition", choices=("validation",), default="validation")
    parser.add_argument("--merge", type=Path)
    parser.set_defaults(limit=None)
    args = parser.parse_args()
    supplied = (args.shard_index, args.dataset, args.config, args.output)
    if args.merge is None and any(value is None for value in supplied):
        parser.error("shard mode requires --shard-index, --dataset, --config and --output")
    if args.merge is not None and any(value is not None for value in supplied):
        parser.error("--merge is a separate mode; provide only the parent output directory")
    return args


def shard_inputs(rows, manifest, index, count):
    if len(rows) != 25 or any(row["review_format"] != "evidence_v2" for row in rows):
        raise ValueError("checkpoint selection requires all 25 evidence_v2 validation reviews")
    whole_ids = [row["id"] for row in rows]
    selected = [row for position, row in enumerate(rows) if position % count == index]
    manifest = copy.deepcopy(manifest)
    manifest.update(whole_source_ids=whole_ids, whole_source_ids_sha256=ids_hash(whole_ids),
                    whole_source_samples=len(rows), sample_ids=[row["id"] for row in selected], samples=len(selected),
                    shard={"index": index, "count": count, "assignment": "row_index_modulo"},
                    extra_code={WRAPPER: file_hash(ROOT / WRAPPER)})
    return selected, manifest


def load_merge(output):
    if (output / "combined").exists():
        raise FileExistsError("the merged report already exists")
    shards = [read_shard(output / f"shard-{index}", index) for index in range(3)]
    reference = shards[0]["manifest"]
    common = ("dataset", "partition", "split_sha256", "config", "config_sha256", "adapter_sha256", "generation",
              "lm_format_enforcer", "code_sha256", "extra_code", "whole_source_ids", "whole_source_ids_sha256",
              "whole_source_samples")
    for shard in shards[1:]:
        if any(shard["manifest"][name] != reference[name] for name in common):
            raise ValueError("checkpoint shards use different source data, adapters, code or generation settings")
    whole_ids = reference["whole_source_ids"]
    if (len(whole_ids) != 25 or len(set(whole_ids)) != 25 or reference["whole_source_samples"] != 25
            or ids_hash(whole_ids) != reference["whole_source_ids_sha256"]):
        raise ValueError("the complete source identity must contain exactly 25 unique reviews")
    split_path = Path(reference["dataset"]) / f"{reference['partition']}.jsonl"
    if file_hash(split_path) != reference["split_sha256"]:
        raise ValueError("the source validation split changed after inference")
    source = [row for row in read_rows(split_path) if row["phase"] == "review"]
    if [row["id"] for row in source] != whole_ids:
        raise ValueError("the shard mother set differs from the complete validation review set")
    predictions = [row for shard in shards for row in shard["rows"]]
    by_id = {row["id"]: row for row in predictions}
    if len(predictions) != 25 or len(by_id) != 25 or set(by_id) != set(whole_ids):
        raise ValueError("shard predictions contain repeated, missing or unknown reviews")
    validate_prediction_sources(reference, source, by_id)
    manifest = {key: copy.deepcopy(value) for key, value in reference.items() if key != "shard"}
    manifest.update(sample_ids=whole_ids, samples=len(whole_ids),
                    shard_sources=[{"index": index, **shard["artifacts"]} for index, shard in enumerate(shards)],
                    merge_checks={"all_25_source_reviews_once": True, "source_prompt_and_image_hashes_match": True,
                                  "identical_config_adapter_code_generation": True, "source_order_restored": True})
    ordered = [by_id[identifier] for identifier in whole_ids]
    models = [shard["summary"]["model"] for shard in shards]
    model_metrics = {"shards": models, "model_loads": len(models),
                     "load_seconds_sum": sum(model["load_seconds"] for model in models),
                     "peak_allocated_mib_max": max(model["peak_allocated_mib"] for model in models),
                     "peak_reserved_mib_max": max(model["peak_reserved_mib"] for model in models),
                     "inference_seconds_sum": sum(row["elapsed_seconds"] for row in ordered)}
    return manifest, ordered, model_metrics


def read_shard(directory, index):
    manifest = read_json(directory / "manifest.json")
    summary = read_json(directory / "summary.json")
    rows = read_rows(directory / "predictions.jsonl")
    expected = manifest["whole_source_ids"][index::3]
    if (manifest["shard"] != {"index": index, "count": 3, "assignment": "row_index_modulo"}
            or manifest["sample_ids"] != expected or manifest["samples"] != len(expected)
            or [row["id"] for row in rows] != expected
            or not summary["completed"] or summary["planned"] != len(expected)
            or summary["metrics"] != metrics(rows)):
        raise ValueError(f"shard-{index} must contain its complete assigned reviews exactly once")
    return {"manifest": manifest, "summary": summary, "rows": rows,
            "artifacts": {name: {"path": str((directory / name).resolve().relative_to(ROOT)),
                                  "sha256": file_hash(directory / name)}
                          for name in ("manifest.json", "predictions.jsonl", "summary.json")}}


def validate_prediction_sources(manifest, source, predictions):
    original = read_rows(ROOT / "datasets/sft-main-10apps-v3/validation.jsonl")
    matched = {identifier for row in original if row["phase"] == "review"
               for identifier in (row["id"], row["derived_provenance"]["source_sample_id"])}
    for sample in source:
        row = predictions[sample["id"]]
        provenance = sample["derived_provenance"]
        messages = sample["messages"][:-1]
        prompt_hash = hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        label = json.loads(sample["messages"][-1]["content"])["verdict"]
        curriculum = sample.get("sample_kind") == "review_curriculum" or provenance.get("curriculum")
        cohort = ("new_curriculum" if curriculum else "matched_validation"
                  if provenance.get("source_sample_id") in matched else "original_training")
        if (row["source_run_id"] != provenance["source_run_id"]
                or row["source_sample_id"] != provenance.get("source_sample_id")
                or row["case_id"] != sample["case_id"] or row["review_format"] != sample["review_format"]
                or row["label"] != label or row["cohort"] != cohort
                or row["adapter_sha256"] != manifest["adapter_sha256"] or row["prompt_sha256"] != prompt_hash):
            raise ValueError(f"prediction differs from its mother-set source: {sample['id']}")
        images = [Path(manifest["dataset"]) / item["image"] for message in messages
                  for item in message["content"] if item["type"] == "image"]
        if len(images) != len(row["source_image_hashes"]):
            raise ValueError(f"prediction image count differs from source: {sample['id']}")
        for path, saved in zip(images, row["source_image_hashes"]):
            if path.resolve() != Path(saved["path"]).resolve() or file_hash(path) != saved["sha256"]:
                raise ValueError(f"prediction source image differs: {path}")


def ids_hash(identifiers):
    return hashlib.sha256(json.dumps(identifiers, ensure_ascii=False).encode()).hexdigest()


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
