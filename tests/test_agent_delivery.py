import json
import sys
from types import SimpleNamespace

import pytest

from guiqa import doctor as diagnostic
from guiqa.report import render


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    monkeypatch.setattr(diagnostic.shutil, "which", lambda command: None if command == "docker" else command)
    monkeypatch.setattr(diagnostic.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout="available", stderr=""))
    monkeypatch.setattr(diagnostic, "browser_smoke", lambda output: {"corner_center_clicks": 5})
    monkeypatch.setattr(diagnostic, "importlib", SimpleNamespace(import_module=lambda name: object()))


def create_model(root):
    directory = root / "model"
    directory.mkdir()
    for name in ("config.json", "tokenizer_config.json", "tokenizer.json", "preprocessor_config.json"):
        (directory / name).write_text("{}")
    (directory / "model.safetensors.index.json").write_text(json.dumps(
        {"weight_map": {"first": "model-1.safetensors", "second": "model-2.safetensors"}}))
    (directory / "model-1.safetensors").write_bytes(b"first shard")
    (directory / "model-2.safetensors").write_bytes(b"second shard")
    return directory


def test_cpu_doctor_requires_browser_and_php_without_claiming_model_ready(tmp_path, environment):
    result = diagnostic.doctor(tmp_path)
    assert result["ready"]
    assert result["required_checks"] == ["browser", "native_php"]
    assert not result["checks"]["cuda"]["ok"]
    assert not result["checks"]["docker"]["ok"]
    assert "model_dependencies" not in result["checks"]


def test_configured_doctor_reports_missing_weight_shard_and_adapter(tmp_path, environment, monkeypatch):
    directory = create_model(tmp_path)
    (directory / "model-2.safetensors").unlink()
    monkeypatch.setattr(diagnostic, "cuda_check", lambda: {"ok": True})
    result = diagnostic.doctor(tmp_path, {"model_path": "model", "adapter_path": "adapter"})
    assert not result["ready"]
    assert result["checks"]["model_files"]["missing"] == [str(directory / "model-2.safetensors")]
    assert result["checks"]["adapter_files"]["missing"] == [
        str(tmp_path / "adapter/adapter_config.json"), str(tmp_path / "adapter/adapter_model.safetensors")]


def test_configured_doctor_requires_allocated_cuda_without_loading_weights(tmp_path, environment):
    create_model(tmp_path)
    result = diagnostic.doctor(tmp_path, {"model_path": "model"})
    assert not result["ready"]
    assert result["checks"]["model_files"]["ok"]
    assert "CUDA_VISIBLE_DEVICES" in result["checks"]["cuda"]["error"]


def test_cuda_check_only_uses_explicit_allocation(monkeypatch):
    allocations = []
    torch = SimpleNamespace(__version__="test", cuda=SimpleNamespace(get_device_name=lambda index: "allocated"))
    torch.ones = lambda count, device: allocations.append(device) or SimpleNamespace(
        sum=lambda: SimpleNamespace(item=lambda: 1))
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    assert not diagnostic.cuda_check()["ok"]
    assert allocations == []
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "3")
    assert diagnostic.cuda_check()["ok"]
    assert allocations == ["cuda:0"]
    assert diagnostic.os.environ["CUDA_VISIBLE_DEVICES"] == "3"


def test_dependency_failure_keeps_the_original_error(tmp_path, environment, monkeypatch):
    create_model(tmp_path)

    def import_module(name):
        if name == "torchvision":
            raise RuntimeError("operator torchvision::nms does not exist")
        return object()

    monkeypatch.setattr(diagnostic, "importlib", SimpleNamespace(import_module=import_module))
    monkeypatch.setattr(diagnostic, "cuda_check", lambda: {"ok": True})
    result = diagnostic.doctor(tmp_path, {"model_path": "model"})
    assert not result["ready"]
    assert result["checks"]["model_dependencies"]["errors"]["torchvision"] == (
        "RuntimeError: operator torchvision::nms does not exist")


def test_full_runtime_readiness_includes_mcp_configuration(tmp_path, environment, monkeypatch):
    create_model(tmp_path)
    monkeypatch.setattr(diagnostic, "cuda_check", lambda: {"ok": True})
    config = {"model_path": "model", "agent_runtime": {"enabled": True,
        "memory_database": str(tmp_path / "memory.sqlite3"), "memory_namespace": "interactive",
        "mcp_servers": [{"name": "external", "url": "file:///tmp/service"}]}}
    result = diagnostic.doctor(tmp_path, config)
    assert not result["ready"]
    assert result["checks"]["mcp_dependencies"]["ok"]
    assert "external" in result["checks"]["mcp_configuration"]["errors"]
    config["agent_runtime"]["mcp_servers"] = []
    assert diagnostic.doctor(tmp_path, config)["ready"]


@pytest.mark.parametrize("phase, reason, runtime", [
    ("interrupted", "interrupted", False), ("finished", "controlled_review", True)])
def test_report_shows_saved_state_and_screenshots(tmp_path, phase, reason, runtime):
    result = {"case_id": "sweeper-008", "workflow": "target", "agent_verdict": "blocked",
        "oracle_verdict": "inconclusive", "termination_reason": reason, "actions": 1,
        "model_calls": 2, "elapsed_seconds": 1.5, "config_hash": "config"}
    if runtime:
        result["agent_runtime"] = {"context_updates": 1}
    (tmp_path / "result.json").write_text(json.dumps(result))
    state = {"phase": phase, "next_goal": "Save and reopen", "context_updates": 1,
        "compacted_through_step": None, "retrieved_experiences": [], "summary": "Value entered",
        "reflection": "", "plan": [{"step": "Enter value", "status": "done"},
                                    {"step": "Save and reopen", "status": "pending"}]}
    (tmp_path / "task-state.json").write_text(json.dumps(state))
    (tmp_path / "trace.jsonl").write_text(json.dumps(
        {"step": 1, "description": "Entered value", "screenshot": "screenshots/001.png"}) + "\n")
    report = render(tmp_path).read_text()
    assert f"状态：{phase}" in report
    assert f"终止原因：{reason}" in report
    assert "下一步目标：Save and reopen" in report
    assert "待完成步骤：Save and reopen" in report
    assert "[pending] Save and reopen" in report
    assert "![步骤 1](screenshots/001.png)" in report
    assert "[任务状态](task-state.json)" in report
    assert "[本次经验](experience.json)" not in report
