"""Export project metrics and small public runtime examples for Git."""

import hashlib
import json
from pathlib import Path
import shutil

from guiqa.artifacts import write_json


ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "data": "runs/review-expansion-v1/completion.json",
    "sft": "runs/sft-qwen3vl-review-expanded-v1/completion.json",
    "grpo": "runs/rl-review-grpo-expanded-v1/completion.json",
    "comparison": "reports/rl-review-grpo-expanded-v1/comparison.json",
    "runtime": "reports/agent-runtime-20261109-verified/summary.json",
    "verification": "reports/agent-runtime-20261109-verified/runtime-verification.json",
}


def main():
    records = {name: json.loads((ROOT / path).read_text()) for name, path in SOURCES.items()}
    output = ROOT / "docs/results"
    output.mkdir(parents=True, exist_ok=True)
    summary = project_summary(records)
    summary["source_sha256"] = {path: file_hash(ROOT / path) for path in SOURCES.values()}
    write_json(output / "summary.json", summary)
    shutil.copy2(ROOT / "reports/agent-runtime-20261109-verified/tests.xml", output / "tests.xml")
    export_examples(records["runtime"]["episodes"])
    export_regression_fixture()
    print(json.dumps({"summary": "docs/results/summary.json", "examples": "examples/agent-runtime",
                      "tests_passed": summary["runtime"]["tests"]["passed"]}, ensure_ascii=False, indent=2))


def project_summary(records):
    data = records["data"]["packages"]
    grpo = records["grpo"]
    metrics = ("samples", "terminal_verdict_correct", "terminal_verdict_accuracy", "macro_f1",
               "strict_correct", "strict_accuracy", "defects_detected", "defect_recall",
               "defects_false_passed", "normal_false_alarms")
    return {
        "project": "Qwen3-VL GUI Agent", "verified_on": "2026-11-09",
        "data": {"sft_training_samples": data["training"]["counts"]["train"]["samples"],
                 "training_reviews": data["rl_reviews"]["train_reviews"],
                 "development_reviews": data["rl_reviews"]["development_reviews"],
                 "final_test_reviews": data["final_test"]["counts"]["test"]["reviews"],
                 "final_test_parent_scenarios": data["final_test"]["parent_scenarios"]["test"]},
        "sft": {key: records["sft"][key] for key in
                ("optimizer_steps", "selected_epoch", "train_samples", "validation_samples", "adapter_sha256")},
        "grpo": {key: grpo[key] for key in ("optimizer_steps", "pool_samples", "question_exposures",
                 "unique_scheduled_questions", "reward_groups", "task_differential_groups",
                 "reference_unchanged", "adapter_sha256")},
        "evaluation": {partition: {
            model: {metric: records["comparison"][partition][model][metric] for metric in metrics}
            for model in ("sft", "grpo")} for partition in ("validation", "test")},
        "runtime": {
            "architecture_verified": records["verification"]["architecture_verified"],
            "tests": records["verification"]["tests"],
            "cross_task_memory_verified": records["runtime"]["cross_task_memory_verified"],
            "context_compaction_verified": records["runtime"]["context_compaction_verified"],
            "mcp_verified": records["runtime"]["mcp_verified"],
            "episodes": [{key: episode[key] for key in ("run_id", "workflow", "agent_verdict",
                         "oracle_verdict", "actions", "model_calls", "context_updates",
                         "compacted_through_step", "recalled_run_ids", "elapsed_seconds")}
                         for episode in records["runtime"]["episodes"]]},
    }


def export_examples(episodes):
    destination = ROOT / "examples/agent-runtime"
    provenance = {}
    for episode in episodes:
        source = Path(episode["run_directory"])
        name = "edit" if episode["workflow"] == "target" else "create"
        target = destination / name
        target.mkdir(parents=True, exist_ok=True)
        files = [source / name for name in ("report.md", "result.json", "task-state.json", "trace.jsonl",
                 "model.jsonl", "context-events.jsonl", "tool-calls.jsonl", "tool-catalog.json",
                 "experience.json", "junit.xml")]
        files.extend(sorted((source / "screenshots").glob("*.png")))
        for path in files:
            relative = path.relative_to(source)
            copy = target / relative
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, copy)
            provenance[str(copy.relative_to(ROOT))] = {
                "source": str(path.relative_to(ROOT)), "sha256": file_hash(path)}
    write_json(destination / "provenance.json", provenance)


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export_regression_fixture():
    source = ROOT / "runs/20261108T052255Z-c901e41f/trace.jsonl"
    target = ROOT / "tests/fixtures/no-type-creation"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target / "trace.jsonl")
    write_json(target / "provenance.json", {
        "source": str(source.relative_to(ROOT)), "sha256": file_hash(source)})


if __name__ == "__main__":
    main()
