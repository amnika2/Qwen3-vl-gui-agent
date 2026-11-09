"""Qwen3-VL batches with loss on the final assistant JSON and its EOS."""

from pathlib import Path

import torch
from PIL import Image


class QwenSftCollator:
    def __init__(self, processor, dataset_root):
        self.processor = processor
        self.dataset_root = Path(dataset_root)
        self.processor.tokenizer.padding_side = "right"

    def __call__(self, samples):
        texts, prefixes, targets, images, image_counts = [], [], [], [], []
        for sample in samples:
            messages = sample["messages"]
            texts.append(self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=False))
            prefixes.append(self.processor.apply_chat_template(messages[:-1], tokenize=False, add_generation_prompt=True))
            targets.append(messages[-1]["content"] + self.processor.tokenizer.eos_token)
            count = 0
            for message in messages[:-1]:
                for item in message["content"]:
                    if item["type"] == "image":
                        with Image.open(self.dataset_root / item["image"]) as image:
                            images.append(image.convert("RGB"))
                        count += 1
            image_counts.append(count)

        inputs = self.processor(text=texts, images=images, padding=True,
                                add_special_tokens=False, return_tensors="pt")
        prefix_ids = self.processor.tokenizer(prefixes, add_special_tokens=False)["input_ids"]
        target_ids = self.processor.tokenizer(targets, add_special_tokens=False)["input_ids"]
        labels = torch.full_like(inputs["input_ids"], -100)
        for index, (prefix, target, image_count) in enumerate(zip(prefix_ids, target_ids, image_counts)):
            # Rendering has one image_pad per image; processing expands each into its visual tokens.
            visual_tokens = int((inputs["input_ids"][index] == self.processor.image_token_id).sum())
            start = len(prefix) + visual_tokens - image_count
            labels[index, start:start + len(target)] = inputs["input_ids"][index, start:start + len(target)]
        inputs["labels"] = labels
        return inputs
