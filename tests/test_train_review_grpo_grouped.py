import copy
import json
from types import SimpleNamespace

import pytest

from guiqa.review_facts import to_facts
from guiqa.train_review_grpo_grouped import (GroupedFactsReward, InsufficientEffectiveGroups,
                                            grouped_trainer)


def review_sample(identifier, case):
    gold = {
        "action": "finish", "task_kind": "edit",
        "field_readings": [{"field": "Name", "intent": "edit", "requested_value": "new",
            "before": {"value": "new", "step": 1}, "after": {"value": "new", "step": 3},
            "input_matches_request": True, "result_matches_request": True}],
        "submission": {"status": "accepted", "evidence": {"step": 2, "observation": "Saved."}},
        "result_observed": True, "result_steps": [3], "expected_rejection": False,
        "verdict": "pass", "observation": "The reopened value is new.", "evidence_steps": [1, 2, 3],
    }
    return {"id": identifier, "case_id": case, "gold": gold, "steps": {1, 2, 3},
            "profile": "persistence", "reward_rules": {},
            "prompt": [{"role": "user", "content": [{"type": "text", "text": identifier}]}]}


def answers(sample):
    positive = to_facts(sample["gold"])
    negative = copy.deepcopy(positive)
    negative["verdict"] = "fail"
    unknown = copy.deepcopy(positive)
    unknown["field_readings"][0]["field"] = "unrecognized field alias"
    return [json.dumps(answer) for answer in (positive, negative, unknown)]


class FakeProcessor:
    def __init__(self, outputs):
        self.outputs = outputs

    def batch_decode(self, rows, **kwargs):
        return [self.outputs[row[0]] for row in rows]

    def apply_chat_template(self, *args, **kwargs):
        return "prompt"

    def __call__(self, **kwargs):
        import torch

        return {"input_ids": torch.tensor([[7]]), "attention_mask": torch.tensor([[1]])}


def fake_base(reward, patterns):
    torch = pytest.importorskip("torch")
    from torch.nn.utils.rnn import pad_sequence

    class FakeBase:
        def __init__(self):
            self._step = 0
            self.args = SimpleNamespace(gradient_accumulation_steps=8)
            self.state = SimpleNamespace(global_step=0)
            self.tools, self.chat_template_kwargs = None, {}
            self.processing_class = FakeProcessor(answers(next(iter(reward.samples.values()))))
            self.patterns = iter(patterns)
            self.generated = 0
            self.parent_calls = []

        def _generate_single_turn(self, prompts):
            self.generated += 1
            return ([[7]] * 4, [[index, 99] for index in next(self.patterns)], None, {})

        def _generate_and_score_completions(self, inputs):
            self.parent_calls.append(inputs)
            generated = self._generate_single_turn([row["prompt"] for row in inputs])
            completions = [[{"role": "assistant", "content": raw}]
                           for raw in self.processing_class.batch_decode(generated[1])]
            reward(completions, [row["sample_id"] for row in inputs], generated[1])
            prompt_tensors = [torch.tensor(ids) for ids in generated[0]]
            completion_tensors = [torch.tensor(ids) for ids in generated[1]]
            prompt_ids = pad_sequence(prompt_tensors, batch_first=True)
            completion_ids = pad_sequence(completion_tensors, batch_first=True)
            return {"prompt_ids": prompt_ids, "prompt_mask": torch.ones_like(prompt_ids),
                    "completion_ids": completion_ids, "completion_mask": torch.ones_like(completion_ids)}

    return FakeBase


def pool(samples):
    return [{"sample_id": sample["id"], "prompt": sample["prompt"], "images": []} for sample in samples]


def test_facts_reward_keeps_unknown_separate_and_rewards_only_strict_facts(tmp_path):
    sample = review_sample("a", "application-a")
    reward = GroupedFactsReward([sample], tmp_path, {99})
    raw = answers(sample)
    scores, resolutions = reward.score_answers(sample, raw, [[0, 99], [1, 99], [2, 99]])

    assert [row["reward"] for row in resolutions] == [1.0, -1.0, None]
    assert scores[0]["strict_correct"]
    assert scores[2]["needs_review"]
    truncated = reward.preview("a", [[0, 99], [1, 99], [0, 99], [1, 98]], FakeProcessor(raw))
    assert truncated["binary_rewards"] == [1.0, -1.0, 1.0, None]
    assert not truncated["eligible"]


def test_two_questions_reuse_exact_selected_tokens_for_one_training_batch(monkeypatch, tmp_path):
    samples = [review_sample("a", "application-a"), review_sample("b", "application-b")]
    reward = GroupedFactsReward(samples, tmp_path, {99})
    base = fake_base(reward, [[0] * 4, [0, 1, 0, 0], [1, 0, 1, 1]])
    monkeypatch.setattr("guiqa.train_review_grpo_grouped.pilot_trainer", lambda: base)
    trainer = grouped_trainer(reward, pool(samples))()

    result = trainer._generate_and_score_completions([])

    assert trainer.generated == 3
    assert len(trainer.parent_calls) == 1
    assert [row["sample_id"] for row in trainer.parent_calls[0]] == ["a"] * 4 + ["b"] * 4
    assert result["completion_ids"].tolist() == [[index, 99] for index in [0, 1, 0, 0, 1, 0, 1, 1]]
    assert reward.batches == [{"optimizer_step": 1, "groups": [0, 1],
        "sample_ids": ["a", "b"], "case_ids": ["application-a", "application-b"],
        "labels": ["pass", "pass"], "cached_completion_tokens_match": True,
        "cached_prompt_tokens_match": True, "processor_forward_prompt_tokens_match": True}]
    selections = [json.loads(line) for line in (tmp_path / "sampling-selections.jsonl").read_text().splitlines()]
    assert [row["selected"] for row in selections] == [False, True, True]


@pytest.mark.parametrize("pattern", [[0] * 4, [1] * 4, [0, 1, 0, 2]])
def test_insufficient_effective_groups_stop_before_the_training_pass(monkeypatch, tmp_path, pattern):
    samples = [review_sample("a", "application-a"), review_sample("b", "application-b")]
    reward = GroupedFactsReward(samples, tmp_path, {99})
    base = fake_base(reward, [pattern] * 4)
    monkeypatch.setattr("guiqa.train_review_grpo_grouped.pilot_trainer", lambda: base)
    trainer = grouped_trainer(reward, pool(samples), max_samples=2)()

    with pytest.raises(InsufficientEffectiveGroups):
        trainer._generate_and_score_completions([])

    assert trainer.generated == 4
    assert not trainer.parent_calls
    assert not reward.groups
    stop = json.loads((tmp_path / "sampling-stops.jsonl").read_text())
    assert stop["completed_optimizer_steps"] == 0
    assert stop["sampled_groups"] == 4


def test_selection_prefers_another_application_without_combining_candidates(monkeypatch, tmp_path):
    samples = [review_sample("a", "same-application"), review_sample("b", "same-application"),
               review_sample("c", "another-application")]
    reward = GroupedFactsReward(samples, tmp_path, {99})
    base = fake_base(reward, [[0, 1, 0, 0], [1, 0, 1, 1], [0, 0, 1, 0]])
    monkeypatch.setattr("guiqa.train_review_grpo_grouped.pilot_trainer", lambda: base)
    trainer = grouped_trainer(reward, pool(samples), max_samples=3)()

    result = trainer._generate_and_score_completions([])

    assert reward.batches[0]["sample_ids"] == ["a", "c"]
    assert trainer.generated == 3
    assert result["completion_ids"].tolist() == [[index, 99] for index in [0, 1, 0, 0, 0, 0, 1, 0]]


def test_different_questions_can_share_an_application_when_scan_budget_exhausts(monkeypatch, tmp_path):
    samples = [review_sample("a", "same-application"), review_sample("b", "same-application")]
    reward = GroupedFactsReward(samples, tmp_path, {99})
    base = fake_base(reward, [[0, 1, 0, 0], [1, 0, 1, 1]])
    monkeypatch.setattr("guiqa.train_review_grpo_grouped.pilot_trainer", lambda: base)
    trainer = grouped_trainer(reward, pool(samples), max_samples=2)()

    trainer._generate_and_score_completions([])

    assert reward.batches[0]["sample_ids"] == ["a", "b"]
    assert len(set(reward.batches[0]["case_ids"])) == 1


def test_real_trl_parent_runs_a_tiny_two_question_optimizer_step(monkeypatch, tmp_path):
    torch = pytest.importorskip("torch")
    pytest.importorskip("trl")
    from datasets import Dataset
    from peft import PeftModel
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from transformers import PreTrainedTokenizerFast, Qwen3VLConfig, Qwen3VLForConditionalGeneration

    from guiqa.train_review_grpo import adapter_summary, grpo_config
    from guiqa.train_review_grpo_pilot import pilot_trainer
    from guiqa.train_review_grpo_grouped import grouped_monitor
    from guiqa.train_sft import initialize_language_adapter

    samples = [review_sample("a", "application-a"), review_sample("b", "application-b")]
    positive, negative, _ = answers(samples[0])
    tokenizer = PreTrainedTokenizerFast(tokenizer_object=Tokenizer(WordLevel(
        {"[PAD]": 0, "[EOS]": 1, "[UNK]": 2, "prompt": 3, positive: 4, negative: 5},
        unk_token="[UNK]")), pad_token="[PAD]", eos_token="[EOS]", unk_token="[UNK]")
    tokenizer.chat_template = "{% for message in messages %}prompt{% endfor %}"
    model_config = Qwen3VLConfig(
        text_config={"vocab_size": 16, "hidden_size": 32, "intermediate_size": 64,
            "num_hidden_layers": 1, "num_attention_heads": 4, "num_key_value_heads": 2,
            "head_dim": 8, "max_position_embeddings": 32, "use_cache": False,
            "pad_token_id": 0, "eos_token_id": 1, "bos_token_id": 3,
            "rope_scaling": {"rope_type": "default", "mrope_section": [2, 1, 1]}},
        vision_config={"depth": 1, "hidden_size": 32, "intermediate_size": 64,
            "num_heads": 4, "out_hidden_size": 32, "patch_size": 2,
            "temporal_patch_size": 1, "spatial_merge_size": 2,
            "num_position_embeddings": 16, "deepstack_visual_indexes": []},
        image_token_id=12, video_token_id=13, vision_start_token_id=14, vision_end_token_id=15,
        pad_token_id=0, eos_token_id=1, bos_token_id=3)
    initial, _ = initialize_language_adapter(Qwen3VLForConditionalGeneration(model_config),
        {"lora_rank": 2, "lora_alpha": 4, "lora_dropout": 0.0}, tmp_path)
    initial.save_pretrained(tmp_path / "initial")
    actor = PeftModel.from_pretrained(Qwen3VLForConditionalGeneration(model_config),
                                    tmp_path / "initial", is_trainable=True)
    before = adapter_summary(actor, "default")["sha256"]
    output = tmp_path / "trial"
    output.mkdir()
    reward = GroupedFactsReward(samples, output, {0, 1})

    class FixedSamplingPilot(pilot_trainer()):
        def _generate_single_turn(self, prompts):
            return ([[3]] * 4, [[4, 1], [5, 1], [4, 1], [4, 1]], None, {})

    monkeypatch.setattr("guiqa.train_review_grpo_grouped.pilot_trainer", lambda: FixedSamplingPilot)
    arguments = grpo_config(output, cpu=True, max_steps=1, gradient_accumulation_steps=8,
        generation_batch_size=8, bf16=False, gradient_checkpointing=False, max_completion_length=8)
    dataset = Dataset.from_list(pool(samples))
    monitor = grouped_monitor(output, reward, before)
    trainer = grouped_trainer(reward, dataset)(model=actor, args=arguments, processing_class=tokenizer,
        train_dataset=dataset, reward_funcs=reward, callbacks=[monitor])

    trainer.train()

    assert trainer.state.global_step == 1
    assert len(reward.groups) == 2
    assert reward.batches[0]["sample_ids"] == ["a", "b"]
    assert monitor.updates[0]["gradient_l2"] > 0
    assert adapter_summary(actor, "default")["sha256"] != before
    assert adapter_summary(actor, "ref")["sha256"] == before
