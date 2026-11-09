import copy
import json

import pytest

from guiqa.review_facts import to_facts
from guiqa.rl_review_expanded_reward import ExpandedFactsReward
from guiqa.train_review_grpo_expanded import balanced_schedule, expanded_trainer, training_monitor


def review_samples():
    samples = []
    for label in ("pass", "fail", "blocked"):
        for case in ("a", "b"):
            gold = {
                "action": "finish", "task_kind": "edit",
                "field_readings": [{"field": "Name", "intent": "edit", "requested_value": "new",
                    "before": {"value": "new", "step": 1},
                    "after": {"value": "new", "step": 3},
                    "input_matches_request": True, "result_matches_request": True}],
                "submission": {"status": "accepted", "evidence": {"step": 2, "observation": "Saved."}},
                "result_observed": True, "result_steps": [3], "expected_rejection": False,
                "verdict": label, "observation": "The record was reopened.", "evidence_steps": [1, 2, 3],
            }
            if label == "fail":
                gold["field_readings"][0]["after"]["value"] = "old"
                gold["field_readings"][0]["result_matches_request"] = False
            elif label == "blocked":
                gold["field_readings"][0].update(before=None, after=None,
                    input_matches_request=None, result_matches_request=None)
                gold.update(submission={"status": "unknown", "evidence": None},
                            result_observed=False, result_steps=[], evidence_steps=[1])
            identifier = label + "-" + case
            samples.append({"id": identifier, "case_id": case, "gold": gold, "steps": {1, 2, 3},
                "profile": "persistence", "reward_rules": {},
                "prompt": [{"role": "user", "content": [{"type": "text", "text": identifier}]}]})
    return samples


def test_real_trl_three_question_update_and_resume_skip(monkeypatch, tmp_path):
    torch = pytest.importorskip("torch")
    pytest.importorskip("trl")
    from accelerate import skip_first_batches
    from datasets import Dataset
    from peft import PeftModel
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from transformers import (PreTrainedTokenizerFast, Qwen3VLConfig,
                              Qwen3VLForConditionalGeneration, TrainerCallback)

    from guiqa.train_review_grpo import adapter_summary, grpo_config
    from guiqa.train_sft import initialize_language_adapter

    torch.set_num_threads(2)
    samples = review_samples()
    schedule = balanced_schedule(samples, 2, 42)
    ordered = [samples[index] for index in schedule]
    assert [row["gold"]["verdict"] for row in ordered] == ["pass", "fail", "blocked"] * 2
    vocabulary = {"[PAD]": 0, "[EOS]": 1, "[UNK]": 2, "prompt": 3}
    tokens = {}
    for sample in samples[::2]:
        positive = to_facts(sample["gold"])
        negative = copy.deepcopy(positive)
        negative["verdict"] = "fail" if sample["gold"]["verdict"] != "fail" else "pass"
        unknown = copy.deepcopy(positive)
        unknown["field_readings"][0]["field"] = "unverified alias"
        for kind, answer in (("positive", positive), ("negative", negative), ("unknown", unknown)):
            text = json.dumps(answer)
            vocabulary[text] = len(vocabulary)
            tokens[sample["gold"]["verdict"], kind] = vocabulary[text]
    tokenizer = PreTrainedTokenizerFast(tokenizer_object=Tokenizer(WordLevel(vocabulary,
        unk_token="[UNK]")), pad_token="[PAD]", eos_token="[EOS]", unk_token="[UNK]")
    tokenizer.chat_template = "prompt"
    config = Qwen3VLConfig(
        text_config={"vocab_size": 32, "hidden_size": 32, "intermediate_size": 64,
            "num_hidden_layers": 1, "num_attention_heads": 4, "num_key_value_heads": 2,
            "head_dim": 8, "max_position_embeddings": 32, "use_cache": False,
            "pad_token_id": 0, "eos_token_id": 1, "bos_token_id": 3,
            "rope_scaling": {"rope_type": "default", "mrope_section": [2, 1, 1]}},
        vision_config={"depth": 1, "hidden_size": 32, "intermediate_size": 64,
            "num_heads": 4, "out_hidden_size": 32, "patch_size": 2,
            "temporal_patch_size": 1, "spatial_merge_size": 2,
            "num_position_embeddings": 16, "deepstack_visual_indexes": []},
        image_token_id=28, video_token_id=29, vision_start_token_id=30, vision_end_token_id=31,
        pad_token_id=0, eos_token_id=1, bos_token_id=3)
    initial, _ = initialize_language_adapter(Qwen3VLForConditionalGeneration(config),
        {"lora_rank": 2, "lora_alpha": 4, "lora_dropout": 0.0}, tmp_path)
    initial.save_pretrained(tmp_path / "initial")
    output = tmp_path / "training"
    output.mkdir()
    dataset = Dataset.from_list([{"sample_id": row["id"], "prompt": row["prompt"], "images": []}
                                 for row in ordered])
    by_id = {row["id"]: row for row in samples}

    class FixedSamplingTrainer(expanded_trainer()):
        def _generate_and_score_completions(self, inputs):
            self.seen_ids = [row["sample_id"] for row in inputs]
            result = super()._generate_and_score_completions(inputs)
            self.seen_advantages = result["advantages"].detach().cpu()
            return result

        def _generate_single_turn(self, prompts):
            kinds = ("positive", "negative", "unknown", "positive")
            completions = [[tokens[by_id[identifier]["gold"]["verdict"], kinds[index % 4]], 1]
                           for index, identifier in enumerate(self.seen_ids)]
            return [[3]] * len(prompts), completions, None, {}

    class StopAfterFirst(TrainerCallback):
        def on_step_end(self, args, state, control, **kwargs):
            if state.global_step == 1:
                control.should_training_stop = True
                control.should_save = True

    arguments = grpo_config(output, cpu=True, max_steps=2, gradient_accumulation_steps=12,
        generation_batch_size=12, save_strategy="steps", save_steps=1,
        bf16=False, gradient_checkpointing=False, max_completion_length=8)
    actor = PeftModel.from_pretrained(Qwen3VLForConditionalGeneration(config),
                                    tmp_path / "initial", is_trainable=True)
    initial_hash = adapter_summary(actor, "default")["sha256"]
    reward = ExpandedFactsReward(samples, output, {0, 1})
    trainer = FixedSamplingTrainer(model=actor, args=arguments, processing_class=tokenizer,
        train_dataset=dataset, reward_funcs=reward,
        callbacks=[training_monitor(output, reward), StopAfterFirst()])
    batches = trainer.get_train_dataloader()
    resumed_first = next(iter(skip_first_batches(batches, 12)))
    expected_resume = [row["id"] for row in ordered[3:] for _ in range(4)]
    assert [row["sample_id"] for row in resumed_first] == expected_resume
    trainer.train()
    assert trainer.state.global_step == 1
    assert trainer.seen_ids == [row["id"] for row in ordered[:3] for _ in range(4)]
    assert [group["label"] for group in reward.groups] == ["pass", "fail", "blocked"]
    assert all(group["source_rewards"] == [1.0, -1.0, None, 1.0] for group in reward.groups)
    torch.testing.assert_close(trainer.seen_advantages[[2, 6, 10]], torch.zeros(3), atol=1e-6, rtol=0)
    updated_hash = adapter_summary(actor, "default")["sha256"]
    assert updated_hash != initial_hash
    assert adapter_summary(actor, "ref")["sha256"] == initial_hash
    checkpoint = output / "checkpoint-1"
    assert all((checkpoint / name).is_file() for name in
               ("optimizer.pt", "scheduler.pt", "trainer_state.json", "rng_state.pth",
                "adapter_model.safetensors", "ref/adapter_model.safetensors"))
    restored = {}

    class RestoreProbe(TrainerCallback):
        def on_train_begin(self, args, state, control, model, optimizer, **kwargs):
            restored["actor"] = adapter_summary(model, "default")["sha256"]
            restored["reference"] = adapter_summary(model, "ref")["sha256"]
            restored["optimizer_step"] = max(value["step"].item() for value in optimizer.state.values())

    actor2 = PeftModel.from_pretrained(Qwen3VLForConditionalGeneration(config),
                                     tmp_path / "initial", is_trainable=True)
    reward2 = ExpandedFactsReward(samples, output, {0, 1})
    reward2.groups = [json.loads(line) for line in (output / "reward-groups.jsonl").read_text().splitlines()]
    resumed = FixedSamplingTrainer(model=actor2, args=arguments, processing_class=tokenizer,
        train_dataset=dataset, reward_funcs=reward2,
        callbacks=[training_monitor(output, reward2), RestoreProbe()])
    resumed.train(resume_from_checkpoint=str(checkpoint))
    assert resumed.state.global_step == 2
    assert restored == {"actor": updated_hash, "reference": initial_hash, "optimizer_step": 1.0}
    assert resumed.seen_ids == expected_resume
    assert len(reward2.groups) == 6
    assert adapter_summary(actor2, "ref")["sha256"] == initial_hash
    torch.testing.assert_close(resumed.seen_advantages[[2, 6, 10]], torch.zeros(3), atol=1e-6, rtol=0)
    updates = [json.loads(line) for line in (output / "update-steps.jsonl").read_text().splitlines()]
    assert [row["optimizer_step"] for row in updates] == [1, 2]
    assert [[group["label"] for group in row["groups"]] for row in updates] == [
        ["pass", "fail", "blocked"], ["pass", "fail", "blocked"]]
