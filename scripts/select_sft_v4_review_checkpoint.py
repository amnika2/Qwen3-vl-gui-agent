"""Select an existing v4 checkpoint from matched training-application review validation."""

import argparse
import json
import shutil
from pathlib import Path

import yaml

from guiqa.artifacts import write_json
from guiqa.cli import load_config
from guiqa.sft_v2 import checksum, read_json
from summarize_sft_v4_validation import load_group, verify_pairs


SELECTION_METRIC = "training_app_review_correctness"
SELECTION_RULES = [
    "maximize correct reviews on all 25 training-application development samples",
    "then minimize label != pass and prediction == pass",
    "then minimize full training-application validation eval_loss",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epoch1", type=Path,
                        default=Path("reports/sft-v4-checkpoint-85-validation/parts/combined"))
    parser.add_argument("--epoch2", type=Path, default=Path("reports/sft-v4-protocol/v4-new"))
    parser.add_argument("--source-completion", type=Path,
                        default=Path("runs/sft-qwen3vl-10apps-v4/completion.json"))
    parser.add_argument("--output", type=Path, default=Path("runs/sft-qwen3vl-10apps-v4-review-selected"))
    parser.add_argument("--source-config", type=Path, default=Path("configs/agent-review-evidence-v4.yaml"))
    parser.add_argument("--output-config", type=Path,
                        default=Path("configs/agent-review-evidence-v4-selected.yaml"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    chosen, selection, original = choose_checkpoint(
        root, root / args.epoch1, root / args.epoch2, root / args.source_completion)
    source_config, output_config = root / args.source_config, root / args.output_config
    epoch2 = read_json(root / args.epoch2 / "manifest.json")
    if load_config(source_config) != epoch2["config"] or checksum(source_config) != epoch2["config_sha256"]:
        raise ValueError("the source inference configuration must match the evaluated epoch-two adapter")
    output = (root / args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    adapter = output / "adapter"
    adapter.mkdir()
    checkpoint = Path(chosen["checkpoint"])
    for name in ("adapter_model.safetensors", "adapter_config.json"):
        shutil.copy2(checkpoint / name, adapter / name)
    if checksum(adapter / "adapter_model.safetensors") != chosen["adapter_sha256"]:
        raise ValueError("the independently exported adapter differs from the chosen checkpoint")
    shutil.copy2(root / args.source_completion, output / "source-training-completion.json")
    shutil.copy2(Path(__file__), output / "selection-source.py")
    completion = {
        **original, "adapter": str(adapter), "adapter_sha256": chosen["adapter_sha256"],
        "selected_epoch": chosen["epoch"], "best_checkpoint": chosen["checkpoint"],
        "selection_metric": SELECTION_METRIC, "selection_rules": SELECTION_RULES,
        "validation_metrics": {"eval_loss": chosen["full_validation_loss"], "epoch": chosen["epoch"]},
        "source_training_completion": {
            **selection["source_training_completion"],
            "original_selected_epoch": original["selected_epoch"],
            "original_best_checkpoint": original["best_checkpoint"],
            "original_selection_metric": original["selection_metric"],
            "original_validation_metrics": original["validation_metrics"],
        },
        "review_checkpoint_selection": selection,
    }
    write_json(output / "selection.json", selection)
    write_json(output / "completion.json", completion)
    config = yaml.safe_load(source_config.read_text())
    config["adapter_path"] = str(adapter.relative_to(root))
    output_config.write_text(yaml.safe_dump(config, sort_keys=False))
    write_json(output / "export.json", {
        "selected_epoch": chosen["epoch"], "adapter_sha256": chosen["adapter_sha256"],
        "selection_metric": SELECTION_METRIC, "config_path": str(output_config.resolve()),
        "config_sha256": checksum(output_config), "source_config_sha256": checksum(source_config),
        "source_training_unchanged": checksum(root / args.source_completion)
        == selection["source_training_completion"]["sha256"],
    })
    print(json.dumps({"selected_epoch": chosen["epoch"], "checkpoint": chosen["checkpoint"],
                      "adapter_sha256": chosen["adapter_sha256"], "metrics": chosen["metrics"],
                      "output": str(output), "config": str(output_config)}, ensure_ascii=False, indent=2))


def choose_checkpoint(root, epoch1_directory, epoch2_directory, source_completion):
    directories = [Path(epoch1_directory).resolve(), Path(epoch2_directory).resolve()]
    groups = [load_group(directory, 25, {"matched_validation": 23, "new_curriculum": 2}, "evidence_v2")
              for directory in directories]
    first, second = (group["manifest"] for group in groups)
    if set(first["sample_ids"]) != set(second["sample_ids"]):
        raise ValueError("both checkpoints must review exactly the same twenty-five development samples")
    common = ("dataset", "partition", "split_sha256", "generation", "lm_format_enforcer", "code_sha256")
    if any(first[name] != second[name] for name in common):
        raise ValueError("development sources, generation settings and review code must match across checkpoints")
    if first["partition"] != "validation" or any(
        {key: value for key, value in manifest["config"].items() if key != "adapter_path"}
        != {key: value for key, value in second["config"].items() if key != "adapter_path"}
        for manifest in (first, second)
    ):
        raise ValueError("only the adapter may differ between the two validation configurations")
    verify_pairs([group["rows"] for group in groups], compare_prompts=True)
    indexed = [{row["id"]: row for row in group["rows"]} for group in groups]
    source_keys = ("source_run_id", "source_sample_id", "case_id", "label", "cohort", "prompt_sha256", "source_image_hashes")
    for identifier, row in indexed[0].items():
        if any(row[key] != indexed[1][identifier][key] for key in source_keys):
            raise ValueError("development identity, labels, prompts and screenshots must match per sample")
    original = read_json(source_completion)
    if (original["completed"] is not True or original["epoch"] != 2
            or {entry["epoch"] for entry in original["epoch_validation"]} != {1, 2}):
        raise ValueError("selection requires the completed original two-epoch training")
    split = yaml.safe_load((root / "configs/sft-split.yaml").read_text())
    training_cases = set(split["training_pilot"] + split["training_supplementary"])
    if not set(original["validation_case_ids"]) or not set(original["validation_case_ids"]) <= training_cases:
        raise ValueError("the full validation loss must also come only from training applications")
    candidates = []
    for epoch, directory, group in zip((1, 2), directories, groups):
        history = next(entry for entry in original["epoch_validation"] if entry["epoch"] == epoch)
        checkpoint = (root / history["checkpoint"]).resolve()
        manifest, rows = group["manifest"], group["rows"]
        weights = Path(manifest["config"]["adapter_path"]) / "adapter_model.safetensors"
        adapter_hash = checksum(checkpoint / "adapter_model.safetensors")
        if checksum(weights) != adapter_hash or manifest["adapter_sha256"] != adapter_hash:
            raise ValueError(f"epoch {epoch}: development predictions must use its saved training checkpoint")
        if epoch == 2 and (adapter_hash != original["adapter_sha256"]
                           or checksum(root / original["adapter"] / "adapter_model.safetensors") != adapter_hash):
            raise ValueError("epoch two must match the original exported training adapter")
        if any(row["prediction"] not in {"pass", "fail", "blocked"}
               or (row["guard"] is not None or row["parse_error"] is not None) and row["prediction"] != "blocked"
               for row in rows):
            raise ValueError("invalid and guarded development predictions must remain blocked")
        defects = [row for row in rows if row["label"] == "fail"]
        detected = sum(row["prediction"] == "fail" for row in defects)
        values = {
            **group["provenance"]["metrics"], "defect_detected": detected, "defect_total": len(defects),
            "defect_recall": detected / len(defects),
            "nonpass_predicted_pass": sum(row["label"] != "pass" and row["prediction"] == "pass" for row in rows),
            "blocked": sum(row["prediction"] == "blocked" for row in rows),
        }
        candidates.append({
            "epoch": epoch, "checkpoint": str(checkpoint), "adapter_sha256": adapter_hash,
            "full_validation_loss": history["eval_loss"], "metrics": values,
            "development_report": str(directory),
            "source_sha256": {name: checksum(directory / name)
                               for name in ("manifest.json", "predictions.jsonl", "summary.json")},
        })
    chosen = min(candidates, key=lambda candidate: (
        -candidate["metrics"]["correct"], candidate["metrics"]["nonpass_predicted_pass"],
        candidate["full_validation_loss"]))
    selection = {
        "selection_metric": SELECTION_METRIC, "rules": SELECTION_RULES,
        "development_reports": {"epoch1": str(directories[0]), "epoch2": str(directories[1])},
        "source_training_completion": {"path": str(Path(source_completion).resolve()), "sha256": checksum(source_completion)},
        "candidates": candidates, "selected_epoch": chosen["epoch"], "selected_checkpoint": chosen["checkpoint"],
        "selected_adapter_sha256": chosen["adapter_sha256"],
        "selection_source": {"path": str(Path(__file__).resolve()), "sha256": checksum(Path(__file__))},
        "sample_ids": second["sample_ids"], "samples": 25,
        "training_applications_only": True, "holdout_used_for_selection": False,
        "identical_sources_labels_prompts_images": True,
        "original_training_completion_preserved": True,
    }
    return chosen, selection, original


if __name__ == "__main__":
    main()
