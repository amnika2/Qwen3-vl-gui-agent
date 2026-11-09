import sys
from types import SimpleNamespace
from unittest.mock import Mock, sentinel

import pytest
import yaml

from guiqa.cli import load_config
from guiqa.model import Qwen


@pytest.mark.parametrize("adapter_path", [None, "/training/checkpoint-final"])
def test_qwen_loads_optional_adapter_without_changing_base_loading(monkeypatch, adapter_path):
    base = Mock()
    base.eval.return_value = base
    adapted = Mock()
    adapted.eval.return_value = adapted
    processor = Mock()
    base_loader = Mock(return_value=base)
    adapter_loader = Mock(return_value=adapted)
    processor_loader = Mock(return_value=processor)
    torch = SimpleNamespace(manual_seed=Mock(), bfloat16=sentinel.bfloat16)
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(
        AutoProcessor=SimpleNamespace(from_pretrained=processor_loader),
        Qwen3VLForConditionalGeneration=SimpleNamespace(from_pretrained=base_loader)))
    monkeypatch.setitem(sys.modules, "peft", SimpleNamespace(
        PeftModel=SimpleNamespace(from_pretrained=adapter_loader)))
    config = {"model_path": "/models/qwen", "seed": 42, "max_pixels": 802816}
    if adapter_path:
        config["adapter_path"] = adapter_path

    model = Qwen(config)
    model.load()
    model.load()

    base_loader.assert_called_once_with("/models/qwen", dtype=sentinel.bfloat16,
        device_map={"": "cuda:0"}, attn_implementation="sdpa", local_files_only=True)
    processor_loader.assert_called_once_with("/models/qwen", local_files_only=True,
        min_pixels=256 * 28 * 28, max_pixels=802816)
    assert model.processor is processor
    assert model.loaded
    if adapter_path:
        adapter_loader.assert_called_once_with(base, adapter_path, local_files_only=True)
        assert model.model is adapted
        adapted.eval.assert_called_once()
    else:
        adapter_loader.assert_not_called()
        assert model.model is base


@pytest.mark.parametrize("adapter_path", [None, "training/checkpoint-final", "/training/checkpoint-final"])
def test_config_resolves_adapter_path_like_model_path(tmp_path, adapter_path):
    config = {"model_path": "vendor/models/qwen", "seed": 42, "max_actions": 30,
        "wall_seconds": 600, "max_new_tokens": 384, "max_pixels": 802816, "history_actions": 8}
    if adapter_path:
        config["adapter_path"] = adapter_path
    directory = tmp_path / "configs"
    directory.mkdir()
    path = directory / "agent.yaml"
    path.write_text(yaml.safe_dump(config))

    loaded = load_config(path)

    assert loaded["model_path"] == str(tmp_path / "vendor/models/qwen")
    if adapter_path:
        assert loaded["adapter_path"] == str((tmp_path / adapter_path).resolve())
    else:
        assert "adapter_path" not in loaded
