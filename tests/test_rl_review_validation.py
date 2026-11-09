import copy
import json
from pathlib import Path

import pytest

from guiqa.rl_review_validation import (
    evaluate_adapter, facts_rows, merge_validation, score_completion, summarize_validation, terminal_verdict,
    validation_rows,
)


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "datasets/rl-review-pilot-v2"


def test_actual_development_partition_is_independent_and_contains_all_39_reviews():
    rows = validation_rows(DATASET)
    assert len(rows) == 39
    assert all(row["data_split"] == "validation" for row in rows)
    selected = validation_rows(DATASET, 9)
    labels = [json.loads(row["messages"][-1]["content"])["verdict"] for row in selected]
    assert labels == ["fail", "pass", "blocked"] * 3


def test_development_rejects_a_reused_training_source(tmp_path):
    train = json.loads((DATASET / "train.jsonl").read_text().splitlines()[0])
    validation = json.loads((DATASET / "validation.jsonl").read_text().splitlines()[0])
    validation["derived_provenance"]["source_run_id"] = train["derived_provenance"]["source_run_id"]
    (tmp_path / "train.jsonl").write_text(json.dumps(train) + "\n")
    (tmp_path / "validation.jsonl").write_text(json.dumps(validation) + "\n")

    with pytest.raises(ValueError, match="share source runs"):
        validation_rows(tmp_path)


@pytest.mark.parametrize("raw,expected", [
    ('{"verdict":"fail"}', "fail"), ('{"verdict":"blocked"}', "blocked"),
    ('{"verdict":"pass"', None), ('[]', None), ('{"verdict":"unknown"}', None),
])
def test_terminal_metric_is_independent_of_structured_schema(raw, expected):
    assert terminal_verdict(raw) == expected


def prediction(identifier, index, label, verdict, strict=False, parse_error=None):
    return {"id": identifier, "sample_index": index, "case_id": "case", "label": label,
            "terminal_verdict": verdict, "terminal_verdict_correct": verdict == label,
            "strict_correct": strict, "parse_error": parse_error, "guard": None,
            "truncated": False, "reward": 1.2 if strict else -1.0}


def test_metrics_separate_false_alarms_defect_detection_and_structured_success():
    rows = [prediction("a", 0, "fail", "fail", strict=True),
            prediction("b", 1, "pass", "fail"),
            prediction("c", 2, "blocked", "blocked", parse_error="missing fields"),
            prediction("d", 3, "fail", "pass")]
    result = summarize_validation(rows, 4)

    assert result["completed"]
    assert result["all"]["terminal_verdict_correct"] == 2
    assert result["all"]["strict_correct"] == 1
    assert result["all"]["normal_false_alarms"] == 1
    assert result["all"]["normal_false_alarm_rate"] == 1
    assert result["all"]["defects_detected"] == 1
    assert result["all"]["defect_recall"] == 0.5
    assert result["all"]["defects_false_passed"] == 1
    assert result["all"]["defect_false_pass_rate"] == 0.5
    assert result["all"]["class_metrics"] == {
        "pass": {"precision": 0, "recall": 0, "f1": 0},
        "fail": {"precision": 0.5, "recall": 0.5, "f1": 0.5},
        "blocked": {"precision": 1, "recall": 1, "f1": 1},
    }
    assert result["all"]["macro_f1"] == 0.5
    assert result["label"]["blocked"]["format_errors"] == 1


def test_real_preflight_selects_13_samples_without_loading_a_model(tmp_path):
    result = evaluate_adapter(ROOT / "runs/sft-qwen3vl-10apps-v4-review-selected/adapter", DATASET,
                              tmp_path, check_only=True, shard_index=1, shards=3)
    manifest = json.loads((tmp_path / "shard-1/manifest.json").read_text())

    assert result["samples"] == 13
    assert result["model_loaded"] is False
    assert manifest["samples"] == 39
    assert manifest["run_specific_reward_rules_applied"] is False
    assert manifest["sharding"]["assigned_ids"] == [row["id"] for row in validation_rows(DATASET)[1::3]]
    assert manifest["generation"]["do_sample"] is False
    assert manifest["generation"]["schema_constrained"] is False


def test_bounded_preflight_records_existing_schema_and_rejects_changed_resume(tmp_path, monkeypatch):
    monkeypatch.setattr("guiqa.rl_review_validation.importlib.metadata.version", lambda name: "0.11.3")
    adapter = ROOT / "runs/sft-qwen3vl-10apps-v4-review-selected/adapter"
    result = evaluate_adapter(adapter, DATASET, tmp_path, check_only=True, schema_constrained=True)
    manifest = json.loads((tmp_path / "manifest.json").read_text())

    assert result["schema_constrained"] is True
    assert result["samples"] == 39
    assert manifest["generation"]["max_observation_characters"] == 512
    assert manifest["generation"]["lm_format_enforcer"] == "0.11.3"
    assert manifest["generation"]["max_new_tokens"] == 1024
    assert manifest["run_specific_reward_rules_applied"] is False
    with pytest.raises(ValueError, match="different inputs or generation settings"):
        evaluate_adapter(adapter, DATASET, tmp_path, check_only=True)


def test_facts_transform_keeps_public_user_evidence_and_original_rows():
    original = validation_rows(DATASET)
    saved = copy.deepcopy(original)
    converted = facts_rows(original)

    assert original == saved
    assert len(converted) == 39
    for before, after in zip(original, converted):
        assert before["messages"][1:-1] == after["messages"][1:-1]
        assert before["review_frames"] == after["review_frames"]
        assert after["review_format"] == "evidence_facts_v1"
        gold = json.loads(after["messages"][-1]["content"])
        assert "observation" not in gold
        assert gold["verdict"] == json.loads(before["messages"][-1]["content"])["verdict"]


def test_facts_preflight_records_compact_schema_and_original_prompt_hashes(tmp_path, monkeypatch):
    monkeypatch.setattr("guiqa.rl_review_validation.importlib.metadata.version", lambda name: "0.11.3")
    adapter = ROOT / "runs/sft-qwen3vl-10apps-v4-review-selected/adapter"
    result = evaluate_adapter(adapter, DATASET, tmp_path, check_only=True, facts_only=True)
    manifest = json.loads((tmp_path / "manifest.json").read_text())

    assert result["schema_constrained"] is True
    assert manifest["generation"]["model_generated_explanations"] is False
    assert "max_observation_characters" not in manifest["generation"]
    assert "facts_schema" in manifest["generation"]["schema"]
    assert "src/guiqa/review_facts.py" in manifest["source_sha256"]
    assert len(manifest["prompt_transform"]["original_prompt_sha256"]) == 39
    assert manifest["prompt_transform"]["user_and_images_unchanged"] is True


@pytest.mark.parametrize("package,partition", [
    ("sft-review-expanded-v1", "validation"),
    ("review-final-test-expanded-v1", "test"),
])
def test_expanded_partitions_use_full_gold_and_the_same_facts_scoring(
        tmp_path, monkeypatch, package, partition):
    monkeypatch.setattr("guiqa.rl_review_validation.importlib.metadata.version", lambda name: "0.11.3")
    dataset = ROOT / "datasets" / package
    adapter = ROOT / "runs/sft-qwen3vl-10apps-v4-review-selected/adapter"
    result = evaluate_adapter(adapter, dataset, tmp_path, check_only=True, facts_only=True,
                              partition=partition)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    full = validation_rows(dataset, partition=partition)
    facts = facts_rows(full)
    packaged = [json.loads(line) for line in (dataset / f"{partition}.jsonl").read_text().splitlines()]

    assert result["model_loaded"] is False
    assert result["samples"] == 150
    assert manifest["partition"] == partition
    assert manifest["holdout_used"] is (partition == "test")
    assert f"{partition}-evidence-v2.jsonl" in manifest["dataset_sha256"]
    assert [row["id"] for row in full] == [row["id"] for row in packaged]
    for transformed, original in zip(facts, packaged):
        assert transformed["messages"] == original["messages"]
        gold = json.loads(transformed["messages"][-1]["content"])
        score, _ = score_completion(json.dumps(gold), gold,
                                     {frame["step"] for frame in transformed["review_frames"]},
                                     transformed["review_profile"], {}, False, True)
        assert score["strict_correct"]


@pytest.mark.parametrize("truncated", [False, True])
def test_facts_score_renders_explanations_before_existing_fact_scoring(truncated):
    row = facts_rows(validation_rows(DATASET))[0]
    gold = json.loads(row["messages"][-1]["content"])
    score, rendered = score_completion(json.dumps(gold), gold,
                                       {frame["step"] for frame in row["review_frames"]},
                                       row["review_profile"], {}, truncated, True)

    assert score["strict_correct"] is (not truncated)
    assert score["parse_error"] is None
    assert "observation" in json.loads(rendered)
    assert "observation" not in gold
    if truncated:
        assert score["reward"] is None
        assert score["transport_error"] == "length_limit_without_eos"


def test_facts_rejects_old_free_text_format_instead_of_scoring_it_as_valid():
    original = validation_rows(DATASET)[0]
    compact = facts_rows([original])[0]
    score, rendered = score_completion(original["messages"][-1]["content"],
                                       json.loads(compact["messages"][-1]["content"]),
                                       {frame["step"] for frame in compact["review_frames"]},
                                       compact["review_profile"], {}, False, True)

    assert rendered is None
    assert score["strict_correct"] is False
    assert score["reward"] == -1
    assert "facts only" in score["parse_error"]


def test_guarded_metric_never_awards_missing_or_truncated_conclusions():
    rows = [prediction("a", 0, "blocked", "pass"),
            prediction("b", 1, "blocked", "blocked", parse_error="invalid"),
            prediction("c", 2, "blocked", "blocked")]
    rows[0]["guard"] = "incomplete_evidence"
    rows[2]["truncated"] = True

    assert summarize_validation(rows, 3)["all"]["reported_guarded_correct"] == 1


@pytest.mark.parametrize("adapter_mismatch", [False, True])
def test_shard_merge_checks_frozen_adapter_and_global_indices(tmp_path, adapter_mismatch):
    base = {"sample_sources": [{"id": "a"}, {"id": "b"}], "adapter_sha256": "same"}
    for index, label in enumerate(("fail", "pass")):
        directory = tmp_path / f"shard-{index}"
        directory.mkdir()
        manifest = {**copy.deepcopy(base), "sharding": {"index": index, "shards": 2,
                                                        "assigned_ids": ["ab"[index]]}}
        if adapter_mismatch and index == 1:
            manifest["adapter_sha256"] = "different"
        (directory / "manifest.json").write_text(json.dumps(manifest))
        (directory / "predictions.jsonl").write_text(
            json.dumps(prediction("ab"[index], index, label, label, strict=True)) + "\n")

    if adapter_mismatch:
        with pytest.raises(ValueError, match="different adapters"):
            merge_validation(tmp_path, 2)
    else:
        result = merge_validation(tmp_path, 2)
        assert result["completed"]
        assert result["all"]["strict_correct"] == 2
        assert len((tmp_path / "predictions.jsonl").read_text().splitlines()) == 2
