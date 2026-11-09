import json
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")
transformers = pytest.importorskip("transformers")

from guiqa.sft_collator import QwenSftCollator


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "datasets/sft-main-10apps-v1"


@pytest.fixture(scope="module")
def processor():
    torch.set_num_threads(2)
    return transformers.AutoProcessor.from_pretrained(
        ROOT / "vendor/models/Qwen3-VL-8B-Instruct", local_files_only=True,
        min_pixels=200704, max_pixels=802816)


def test_single_and_multiimage_masks_padding_and_order(processor):
    rows = [json.loads(line) for line in (DATASET / "train.jsonl").read_text().splitlines()]
    operation = next(row for row in rows if row["phase"] == "operation")
    review = max((row for row in rows if row["phase"] == "review"),
                 key=lambda row: sum(item["type"] == "image" for item in row["messages"][1]["content"]))
    samples = [operation, review]
    batch = QwenSftCollator(processor, DATASET)(samples)
    assert batch["input_ids"].shape == batch["labels"].shape
    assert (batch["labels"][batch["attention_mask"] == 0] == -100).all()
    assert (batch["labels"][batch["input_ids"] == processor.image_token_id] == -100).all()
    counts = [sum(item["type"] == "image" for item in row["messages"][1]["content"]) for row in samples]
    assert len(batch["image_grid_thw"]) == sum(counts)
    assert batch["image_grid_thw"].tolist() == [[1, 46, 66]] * sum(counts)

    for index, sample in enumerate(samples):
        ids = batch["input_ids"][index]
        labels = batch["labels"][index]
        positions = (labels != -100).nonzero().flatten()
        expected = processor.tokenizer(sample["messages"][-1]["content"] + processor.tokenizer.eos_token,
                                       add_special_tokens=False)["input_ids"]
        assert labels[positions].tolist() == expected
        assert positions.tolist() == list(range(int(positions[0]), int(positions[-1]) + 1))
        assert labels[positions[-1]] == processor.tokenizer.eos_token_id
        assert ids[positions[-1] + 1] == processor.tokenizer.encode("\n", add_special_tokens=False)[0]
        assert labels[positions[-1] + 1] == -100
        assert processor.tokenizer.decode(ids[positions[0] - 3:positions[0]]) == "<|im_start|>assistant\n"
        assert (labels[:positions[0]] == -100).all()

    single = QwenSftCollator(processor, DATASET)([review])
    length = int(batch["attention_mask"][1].sum())
    assert torch.equal(batch["input_ids"][1, :length], single["input_ids"][0])
    assert torch.equal(batch["labels"][1, :length], single["labels"][0])
    first_image_patches = int(batch["image_grid_thw"][0].prod())
    assert torch.equal(batch["pixel_values"][first_image_patches:], single["pixel_values"])
