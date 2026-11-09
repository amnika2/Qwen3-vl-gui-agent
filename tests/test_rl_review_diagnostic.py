import copy
import json

import pytest

from guiqa.rl_review_diagnostic import (aggregate, choose_batch_size, sample_diagnostics, select_reviews,
                                      validate_saved, validate_training_reviews)


def row(identifier, case="a", verdict="pass"):
    return {"id": identifier, "case_id": case, "phase": "review", "data_split": "train",
            "review_format": "evidence_v2", "scenario_id": identifier,
            "scenario_fingerprint": "fingerprint-" + identifier,
            "derived_provenance": {"source_run_id": "run-" + identifier},
            "messages": [{"role": "user", "content": [{"type": "text", "text": "public task"}]},
                         {"role": "assistant", "content": json.dumps({"verdict": verdict})}]}


def test_selection_keeps_every_fail_and_balances_remaining_buckets():
    rows = [row(f"f{i}", "a", "fail") for i in range(3)]
    rows += [row(f"{case}-{label}-{i}", case, label)
             for case in ("a", "b") for label in ("pass", "blocked") for i in range(4)]
    selected = select_reviews(rows, 11, 42)
    assert {item["id"] for item in selected[:3]} == {"f0", "f1", "f2"}
    for case in ("a", "b"):
        for label in ("pass", "blocked"):
            assert sum(item["case_id"] == case and json.loads(item["messages"][-1]["content"])["verdict"] == label
                       for item in selected) == 2
    assert selected == select_reviews(list(reversed(rows)), 11, 42)
    with pytest.raises(ValueError, match="all training fail"):
        select_reviews(rows, 2, 42)


@pytest.mark.parametrize("key", ["scenario_id", "scenario_fingerprint"])
def test_ingress_rejects_reused_validation_scenarios(key):
    training, validation = row("training"), row("development")
    validation[key] = training[key]
    with pytest.raises(ValueError, match=key):
        validate_training_reviews([training], [validation],
                                  {"training_pilot": ["a"], "training_supplementary": []})


def test_ingress_rejects_holdout_and_assistant_answers_in_prompt():
    source = row("training", "holdout")
    split = {"training_pilot": ["a"], "training_supplementary": []}
    with pytest.raises(ValueError, match="training applications"):
        validate_training_reviews([source], [], split)
    source["case_id"] = "a"
    source["messages"].insert(0, {"role": "assistant", "content": "gold leaked"})
    with pytest.raises(ValueError, match="no assistant answer"):
        validate_training_reviews([source], [], split)


def test_resume_rejects_candidates_from_other_source_or_shard():
    sources = [row("a"), row("b")]
    manifest = {"candidates_per_sample": 8, "adapter_sha256": "weights",
                "sample_sources": [{"prompt_sha256": "prompt-a"}, {"prompt_sha256": "prompt-b"}]}
    saved = {"id": "a", "candidate_index": 0, "sample_index": 0,
             "adapter_sha256": "weights", "prompt_sha256": "prompt-a"}
    validate_saved([saved], sources, manifest, 0, 2)
    with pytest.raises(ValueError, match="frozen source and shard"):
        validate_saved([saved], sources, manifest, 1, 2)
    with pytest.raises(ValueError, match="duplicate"):
        validate_saved([saved, copy.deepcopy(saved)], sources, manifest, 0, 2)


def test_unverified_rewards_do_not_count_as_zero_variance_group():
    manifest = {"candidates_per_sample": 2,
                "sample_sources": [{"id": "a", "case_id": "app", "label": "fail", "mechanism": "mismatch"}]}
    base = {"id": "a", "verdict_correct": True, "strict_correct": False, "reward": None,
            "raw_sha256": "answer", "needs_review": True, "parse_error": None,
            "truncated": False, "label": "fail", "prediction": "fail", "errors": []}
    predictions = [base, dict(base, raw_sha256="different-answer")]
    diagnostics = sample_diagnostics(predictions, manifest)
    assert diagnostics[0]["fully_sampled"]
    assert diagnostics[0]["zero_reward_variance"] is None
    metrics = aggregate(predictions, diagnostics)
    assert metrics["pass_at_k_verdict"] == 1
    assert metrics["pass_at_k_strict"] == 0
    assert metrics["trusted_reward_coverage"] == 0
    assert metrics["zero_reward_variance_fraction"] is None


@pytest.mark.parametrize("prompt_tokens,expected", [(3000, 4), (5000, 3), (8000, 2), (16000, 1), (20000, 1)])
def test_batch_size_uses_frozen_prompt_token_budget(prompt_tokens, expected):
    assert choose_batch_size(prompt_tokens, {"batch_size": 4, "max_batch_prompt_tokens": 16000}) == expected
