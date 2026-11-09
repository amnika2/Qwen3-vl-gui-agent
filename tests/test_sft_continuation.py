import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("transformers")
pytest.importorskip("peft")
pytest.importorskip("accelerate")

from peft import get_peft_model_state_dict
from transformers import Qwen3VLConfig, Qwen3VLForConditionalGeneration

from guiqa.train_sft import adapter_source, initialize_language_adapter


def test_existing_adapter_loads_trainable_and_source_survives_an_optimizer_update(tmp_path):
    config = Qwen3VLConfig(
        text_config={
            "vocab_size": 64, "hidden_size": 32, "intermediate_size": 64,
            "num_hidden_layers": 1, "num_attention_heads": 4,
            "num_key_value_heads": 2, "head_dim": 8,
            "max_position_embeddings": 32, "use_cache": False,
            "rope_scaling": {"rope_type": "default", "mrope_section": [2, 1, 1]},
        },
        vision_config={
            "depth": 1, "hidden_size": 32, "intermediate_size": 64,
            "num_heads": 4, "out_hidden_size": 32, "patch_size": 2,
            "temporal_patch_size": 1, "spatial_merge_size": 2,
            "num_position_embeddings": 16, "deepstack_visual_indexes": [],
        },
        vision_start_token_id=60, image_token_id=61,
        video_token_id=62, vision_end_token_id=63,
    )
    original, initial_source = initialize_language_adapter(
        Qwen3VLForConditionalGeneration(config),
        {"lora_rank": 2, "lora_alpha": 4, "lora_dropout": 0.0}, tmp_path)
    assert initial_source is None
    with torch.no_grad():
        for name, parameter in original.named_parameters():
            if "lora_B" in name:
                parameter.fill_(0.025)
    original.save_pretrained(tmp_path / "initial")
    expected = {name: value.clone() for name, value in get_peft_model_state_dict(original).items()}

    restored, source = initialize_language_adapter(
        Qwen3VLForConditionalGeneration(config), {"init_adapter_path": "initial"}, tmp_path)
    for name, parameter in get_peft_model_state_dict(restored).items():
        torch.testing.assert_close(parameter, expected[name])
    trainable = [name for name, parameter in restored.named_parameters() if parameter.requires_grad]
    assert trainable and all("lora_" in name for name in trainable)
    assert not any(parameter.requires_grad for parameter in restored.base_model.model.model.visual.parameters())

    optimizer = torch.optim.AdamW((parameter for parameter in restored.parameters() if parameter.requires_grad),
                                 lr=0.01)
    input_ids = torch.tensor([[3, 4, 5, 6, 7, 2]])
    restored(input_ids=input_ids, labels=input_ids).loss.backward()
    optimizer.step()

    assert any(not torch.equal(parameter, expected[name])
               for name, parameter in get_peft_model_state_dict(restored).items())
    assert adapter_source(tmp_path / "initial") == source
