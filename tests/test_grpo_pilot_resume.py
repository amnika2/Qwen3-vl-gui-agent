from types import SimpleNamespace

import pytest
import torch
from peft import LoraConfig, get_peft_model
from transformers import GPT2Config, GPT2LMHeadModel

from guiqa.train_review_grpo_pilot import pilot_trainer


def _model():
    base = GPT2LMHeadModel(GPT2Config(
        vocab_size=16, n_positions=8, n_embd=8, n_layer=1, n_head=1))
    config = LoraConfig(r=2, lora_alpha=2, target_modules=["c_attn"], fan_in_fan_out=True)
    model = get_peft_model(base, config)
    model.add_adapter("ref", config)
    return model


def _fill_adapter(model, adapter, value):
    with torch.no_grad():
        for name, parameter in model.named_parameters():
            if f".{adapter}." in name:
                parameter.fill_(value)


def _adapter_state(model, adapter):
    return {name: parameter.detach().clone() for name, parameter in model.named_parameters()
            if f".{adapter}." in name}


@pytest.mark.parametrize("explicit_model", [False, True])
def test_real_peft_checkpoint_restores_actor_and_frozen_reference(tmp_path, explicit_model):
    assert not torch.cuda.is_initialized()
    saved = _model()
    _fill_adapter(saved, "default", 0.75)
    _fill_adapter(saved, "ref", 0.125)
    expected = {adapter: _adapter_state(saved, adapter) for adapter in ("default", "ref")}
    checkpoint = tmp_path / "checkpoint-10"
    saved.save_pretrained(checkpoint, selected_adapters=["default", "ref"])
    assert (checkpoint / "adapter_model.safetensors").is_file()
    assert (checkpoint / "ref/adapter_model.safetensors").is_file()

    restored = _model()
    _fill_adapter(restored, "default", -0.5)
    _fill_adapter(restored, "ref", -0.25)
    restored.set_adapter("ref")
    holder = SimpleNamespace(model=None if explicit_model else restored)

    pilot_trainer()._load_from_checkpoint(
        holder, str(checkpoint), model=restored if explicit_model else None)

    for adapter in ("default", "ref"):
        actual = _adapter_state(restored, adapter)
        assert actual.keys() == expected[adapter].keys()
        assert all(torch.equal(actual[name], value) for name, value in expected[adapter].items())
    assert restored.active_adapters == ["default"]
    assert all(parameter.requires_grad for name, parameter in restored.named_parameters()
               if ".default." in name)
    assert not any(parameter.requires_grad for name, parameter in restored.named_parameters()
                   if ".ref." in name)
    assert not torch.cuda.is_initialized()
