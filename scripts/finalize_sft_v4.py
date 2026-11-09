"""Record the v4 data, selected two-epoch adapter and preserved v3 release."""

from pathlib import Path

from guiqa.sft_v2 import checksum, read_json, write_json


def main():
    root = Path(__file__).resolve().parents[1]
    data, training, preserved = verify_release(root)
    selected = read_json(root / "runs/sft-qwen3vl-10apps-v4-review-selected/completion.json")
    replay = read_json(root / "reports/sft-v4-review-holdout/summary.json")
    if (checksum(Path(selected["adapter"]) / "adapter_model.safetensors") != selected["adapter_sha256"]
            or selected["adapter_sha256"] != replay["provenance"]["adapter_sha256"]):
        raise ValueError("selected adapter differs from the completed trajectory re-review")
    result = {
        "completed": True, "dataset": "datasets/sft-main-10apps-v4",
        "inference_config": "configs/agent-review-evidence-v4-selected.yaml",
        "adapter": "runs/sft-qwen3vl-10apps-v4-review-selected/adapter",
        "training_export_adapter": "runs/sft-qwen3vl-10apps-v4/adapter",
        "data": {key: data[key] for key in ("samples", "train_samples", "validation_samples",
                 "preserved_operation_samples", "review_samples", "review_labels")},
        "training": training, "v3_preserved": preserved,
        "review_checkpoint_selection": selected["review_checkpoint_selection"],
        "method_validation": {name: read_json(root / "reports/sft-v4-protocol" / name / "summary.json")
                              for name in ("v3-old", "v3-new", "v4-new")},
        "method_validation_comparison": "reports/sft-v4-protocol/comparison.json",
        "holdout_trajectory_re_review": replay,
        "re_review_weights_match_final_selection": True,
        "release_source": "runs/sft-qwen3vl-10apps-v4-review-selected/release-source.json",
        "five_application_evaluation_completed": False,
    }
    write_json(root / "docs/sft-v4-completion.json", result)
    print({"completed": True, "selected_epoch": training["selected_epoch"],
           "epoch_validation": training["epoch_validation"], "adapter": result["adapter"]})


def verify_release(root):
    dataset = root / "datasets/sft-main-10apps-v4"
    run = root / "runs/sft-qwen3vl-10apps-v4"
    data, training = read_json(dataset / "manifest.json"), read_json(run / "completion.json")
    if not training["completed"] or training["epoch"] != 2 or read_json(run / "train-exit.json")["exit_code"] != 0:
        raise ValueError("v4 two-epoch training has not completed")
    hashes = {name: checksum(dataset / name) for name in ("train.jsonl", "validation.jsonl", "manifest.json")}
    if hashes != training["data_sha256"]:
        raise ValueError("v4 dataset changed after training")
    if checksum(run / "adapter/adapter_model.safetensors") != training["adapter_sha256"]:
        raise ValueError("v4 adapter differs from its completion record")
    best = min(training["epoch_validation"], key=lambda row: row["eval_loss"])
    if training["selected_epoch"] != best["epoch"]:
        raise ValueError("v4 selection differs from the training-app validation minimum")
    preserved = read_json(root / "docs/sft-v4-preserved-v3.json")
    if any(checksum(root / path) != value for path, value in preserved.items()):
        raise ValueError("a preserved v3 artifact changed")
    return data, training, {"all_artifacts_unchanged": True, "artifacts_checked": len(preserved)}


if __name__ == "__main__":
    main()
