"""Record the completed v3 release, selected adapter and preserved v2 artifacts."""

from pathlib import Path

from guiqa.sft_v2 import checksum, read_json, write_json


def main():
    root = Path(__file__).resolve().parents[1]
    data, training, verification = verify_completion(root)
    write_json(root / "docs/sft-v3-preservation-result.json", verification)
    summary = {
        "completed": True, "dataset": "datasets/sft-main-10apps-v3",
        "samples": data["samples"], "train_samples": data["train_samples"],
        "validation_samples": data["validation_samples"],
        "preserved_operation_samples": data["preserved_operation_samples"],
        "review_samples": data["review_samples"], "review_labels": data["review_labels"],
        "review_labels_by_split": data["review_labels_by_split"],
        "model": "runs/sft-qwen3vl-10apps-v3/adapter",
        "inference_config": "configs/agent-review-evidence-v3.yaml",
        "training": training, "verification": verification,
        "training_source": "runs/sft-qwen3vl-10apps-v3/training-source.json",
        "five_application_evaluation_started": (root / "runs/sft-lora-v3-workers/launch.json").exists(),
        "five_application_evaluation_completed": (root / "docs/sft-v3-eval-completion.json").exists(),
    }
    write_json(root / "docs/sft-v3-completion.json", summary)
    print({"completed": True, "selected_epoch": training["selected_epoch"],
           "epoch_validation": training["epoch_validation"], "adapter": summary["model"]})


def verify_completion(root):
    dataset = root / "datasets/sft-main-10apps-v3"
    run = root / "runs/sft-qwen3vl-10apps-v3"
    data = read_json(dataset / "manifest.json")
    training = read_json(run / "completion.json")
    exit_status = read_json(run / "train-exit.json")
    if not training["completed"] or training["epoch"] != 2 or exit_status["exit_code"] != 0:
        raise ValueError("v3 two-epoch training has not completed successfully")
    hashes = {name: checksum(dataset / name) for name in ("train.jsonl", "validation.jsonl", "manifest.json")}
    if hashes != training["data_sha256"]:
        raise ValueError("v3 training data differs from the frozen release")
    if checksum(run / "adapter/adapter_model.safetensors") != training["adapter_sha256"]:
        raise ValueError("exported v3 adapter differs from its completion record")
    best = min(training["epoch_validation"], key=lambda row: row["eval_loss"])
    if training["selected_epoch"] != best["epoch"]:
        raise ValueError("the exported model was not selected by the lowest validation loss")
    preserved = read_json(root / "docs/sft-v3-preserved-v2.json")
    if any(checksum(root / name) != expected for name, expected in preserved.items()):
        raise ValueError("a preserved v2 artifact changed")
    verification = {
        "v2_preserved": True, "v2_artifacts_checked": len(preserved),
        "v3_trained_data_matches_frozen_release": True, "exported_adapter_sha256_matches": True,
        "selection_used_training_app_validation": True,
        "selected_epoch": training["selected_epoch"], "selected_validation_loss": best["eval_loss"],
        "training_exit_code": exit_status["exit_code"],
    }
    return data, training, verification


if __name__ == "__main__":
    main()
