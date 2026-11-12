"""Check the dataset26 training boundary and real processor/collator without a model."""

import argparse
import json
from pathlib import Path

import yaml

from guiqa.artifacts import write_json
from guiqa.dataset26_training import checksum, read_jsonl, validate_dataset26


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/sft-train-dataset26-v1.yaml"))
    parser.add_argument("--output", type=Path, default=Path("runs/dataset26-v1/training-compatibility.json"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / args.config).read_text())
    dataset = root / config["dataset"]
    partitions = {split: read_jsonl(dataset / (split + ".jsonl"))
                  for split in ("train", "validation", "test")}
    checked = validate_dataset26(dataset, partitions)
    smoke = processor_smoke(root, config, dataset, partitions["train"])
    report = {"passed": True, **checked, "processor_smoke": smoke,
              "manifest_sha256": checksum(dataset / "manifest.json"),
              "training_started": False, "model_loaded": False,
              "test_scope": "schema and artifact checks only; no model selection"}
    write_json(root / args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def processor_smoke(root, config, dataset, rows):
    import torch
    from transformers import AutoProcessor

    from guiqa.sft_collator import QwenSftCollator

    torch.set_num_threads(2)
    processor = AutoProcessor.from_pretrained(
        root / config["model_path"], local_files_only=True,
        min_pixels=config["min_pixels"], max_pixels=config["max_pixels"])
    operation = next(row for row in rows if row["phase"] == "operation"
                     and json.loads(row["messages"][-1]["content"])["action"] == "upload")
    review = max((row for row in rows if row["phase"] == "review"), key=image_count)
    samples = [operation, review]
    batch = QwenSftCollator(processor, dataset)(samples)
    if not (batch["labels"][batch["attention_mask"] == 0] == -100).all():
        raise ValueError("collator assigns loss to padding")
    if not (batch["labels"][batch["input_ids"] == processor.image_token_id] == -100).all():
        raise ValueError("collator assigns loss to image tokens")
    targets, lengths = [], []
    for index, sample in enumerate(samples):
        expected = processor.tokenizer(
            sample["messages"][-1]["content"] + processor.tokenizer.eos_token,
            add_special_tokens=False)["input_ids"]
        positions = (batch["labels"][index] != -100).nonzero().flatten()
        actual = batch["labels"][index, positions].tolist()
        if actual != expected:
            raise ValueError("collator loss mask differs from the assistant JSON and EOS")
        if positions.tolist() != list(range(int(positions[0]), int(positions[-1]) + 1)):
            raise ValueError("collator assistant loss mask is not contiguous")
        targets.append(len(actual))
        lengths.append(int(batch["attention_mask"][index].sum()))
    return {"samples": [row["id"] for row in samples],
            "image_counts": [image_count(row) for row in samples],
            "image_grid_count": len(batch["image_grid_thw"]),
            "sequence_tokens": lengths, "assistant_tokens": targets,
            "loss_scope": "assistant JSON and EOS only"}


def image_count(row):
    return sum(item["type"] == "image" for message in row["messages"][:-1]
               for item in message["content"])


if __name__ == "__main__":
    main()
