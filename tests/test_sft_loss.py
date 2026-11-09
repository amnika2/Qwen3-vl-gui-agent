import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("transformers")
pytest.importorskip("peft")
pytest.importorskip("accelerate")

from torch.nn import functional as F
from transformers import Qwen3VLConfig, Qwen3VLForConditionalGeneration

from guiqa.train_sft import AssistantTrainer


def test_answer_logits_match_full_masked_loss_and_language_gradients():
    torch.manual_seed(42)
    config = Qwen3VLConfig(
        text_config={
            "vocab_size": 64, "hidden_size": 32, "intermediate_size": 64,
            "num_hidden_layers": 2, "num_attention_heads": 4,
            "num_key_value_heads": 2, "head_dim": 8,
            "max_position_embeddings": 32, "use_cache": False,
            "attention_dropout": 0.0,
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
    model = Qwen3VLForConditionalGeneration(config).cpu().train()
    input_ids = torch.tensor([
        [3, 4, 5, 6, 7, 8, 9, 10, 11, 2, 0, 0],
        [13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 2, 0],
    ])
    batch = {"input_ids": input_ids, "attention_mask": input_ids.ne(0).long()}
    labels = torch.full_like(input_ids, -100)
    labels[0, 4:10] = input_ids[0, 4:10]
    labels[1, 7:11] = input_ids[1, 7:11]
    language_parameters = {
        name: parameter for name, parameter in model.named_parameters()
        if name.startswith(("model.language_model.", "lm_head."))
    }

    full = model(**batch)
    reference_loss = F.cross_entropy(
        full.logits[:, :-1].reshape(-1, config.text_config.vocab_size),
        labels[:, 1:].reshape(-1), ignore_index=-100)
    reference_loss.backward()
    reference_gradients = {
        name: parameter.grad.detach().clone()
        for name, parameter in language_parameters.items()
    }
    model.zero_grad(set_to_none=True)

    loss, outputs = AssistantTrainer.compute_loss(
        None, model, {**batch, "labels": labels.clone()}, return_outputs=True)

    assert outputs.logits.shape == (2, 9, 64)
    torch.testing.assert_close(loss, reference_loss.detach(), rtol=1e-6, atol=1e-7)
    loss.backward()
    assert reference_gradients
    for name, parameter in language_parameters.items():
        torch.testing.assert_close(parameter.grad, reference_gradients[name],
                                   rtol=1e-5, atol=1e-7, msg=name)
