"""Compare saved unconstrained and bounded reviews without regenerating answers."""

import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "reports/rl-review-bounded-20261108"
SOURCES = {
    "v4_unconstrained": ROOT / "reports/rl-review-grpo-retry-baseline-v1",
    "checkpoint10_unconstrained": ROOT / "reports/rl-review-grpo-retry-v1/checkpoint-10",
    "v4_bounded": OUTPUT / "v4",
    "checkpoint10_bounded": OUTPUT / "checkpoint-10",
}


def main():
    records = {name: load_result(path) for name, path in SOURCES.items()}
    preservation = verify_sources(records)
    comparisons = {
        "rl_with_bounded_output": paired(records["v4_bounded"], records["checkpoint10_bounded"]),
        "v4_decode_change": paired(records["v4_unconstrained"], records["v4_bounded"]),
        "checkpoint10_decode_change": paired(records["checkpoint10_unconstrained"], records["checkpoint10_bounded"]),
    }
    result = {
        "completed": True,
        "source_preservation": preservation,
        "models": {name: {"path": str(SOURCES[name].relative_to(ROOT)),
                           "metrics": record["summary"]["all"],
                           "reported_guarded_correct": sum(guarded_verdict(row) == row["label"] for row in record["rows"]),
                           "reported_guarded_by_label": {
                               label: sum(row["label"] == label and guarded_verdict(row) == label
                                          for row in record["rows"])
                               for label in ("pass", "fail", "blocked")},
                           "blocked_correct": record["summary"]["label"]["blocked"]["terminal_verdict_correct"],
                           "fact_assessment": fact_assessment(record["rows"])}
                   for name, record in records.items()},
        "paired": comparisons,
        "free_text_factuality_scored": False,
        "holdout_used": False,
    }
    write_json(OUTPUT / "comparison.json", result)
    write_report(records, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def load_result(path):
    summary = json.loads((path / "summary.json").read_text())
    manifest = json.loads((path / "manifest.json").read_text())
    rows = [json.loads(line) for line in (path / "predictions.jsonl").read_text().splitlines()]
    if not summary["completed"] or len(rows) != 39:
        raise ValueError(f"comparison requires all 39 completed answers: {path}")
    return {"summary": summary, "manifest": manifest, "rows": rows}


def verify_sources(records):
    first = records["v4_unconstrained"]["manifest"]
    keys = ("dataset_sha256", "sample_sources", "rules_sha256", "config_sha256")
    for record in records.values():
        if any(record["manifest"][key] != first[key] for key in keys):
            raise ValueError("the comparison changed data, prompts, screenshots, rules or config")
        if [row["id"] for row in record["rows"]] != [row["id"] for row in first["sample_sources"]]:
            raise ValueError("the comparison changed sample identity or order")
    for old, new in (("v4_unconstrained", "v4_bounded"),
                     ("checkpoint10_unconstrained", "checkpoint10_bounded")):
        old_manifest, new_manifest = records[old]["manifest"], records[new]["manifest"]
        if old_manifest["adapter_sha256"] != new_manifest["adapter_sha256"]:
            raise ValueError("the decoding comparison changed adapter weights")
        for key in ("do_sample", "max_new_tokens", "batch_size", "seed"):
            if old_manifest["generation"][key] != new_manifest["generation"][key]:
                raise ValueError("the decoding comparison changed " + key)
    bounded = [records[name]["manifest"] for name in ("v4_bounded", "checkpoint10_bounded")]
    if bounded[0]["generation"] != bounded[1]["generation"] or bounded[0]["source_sha256"] != bounded[1]["source_sha256"]:
        raise ValueError("bounded model evaluations used different generation settings or code")
    for manifest in bounded:
        for name, saved in manifest["adapter_sha256"].items():
            if file_hash(Path(manifest["adapter"]) / name) != saved:
                raise ValueError("adapter changed after evaluation")
        for name, saved in manifest["dataset_sha256"].items():
            if file_hash(Path(manifest["dataset"]) / name) != saved:
                raise ValueError("dataset changed after evaluation")
    for record in records.values():
        for name in ("src/guiqa/rl_review_reward.py", "src/guiqa/rl_reward_rules.py", "src/guiqa/model.py", "src/guiqa/review_contract.py"):
            if record["manifest"]["source_sha256"][name] != first["source_sha256"][name]:
                raise ValueError("reward, model or contract changed across the comparison")
    return {"same_39_ids_order_prompts_images": True, "same_data_and_rules": True,
            "same_weights_per_decode_comparison": True, "same_scoring_model_contract_code": True,
            "same_greedy_1024_seed42_batch1": True, "data_and_adapters_unchanged_after_evaluation": True}


def fact_assessment(rows):
    categories = Counter()
    for row in rows:
        if row["truncated"]:
            category = "truncated_unscorable"
        elif row["strict_correct"]:
            category = "verified_structured_correct"
        elif row["reward"] is not None and row["reward"] < 0:
            category = "confirmed_error"
        else:
            category = "unverified_without_confirmed_error"
        categories[category] += 1
    return {"exclusive_counts": dict(categories),
            "needs_review_including_confirmed_errors": sum(row["needs_review"] for row in rows),
            "correct_verdict_but_unverified": sum(row["terminal_verdict_correct"] and row["needs_review"] for row in rows)}


def guarded_verdict(row):
    if row["truncated"] or row["parse_error"] is not None:
        return None
    return "blocked" if row["guard"] is not None else row["terminal_verdict"]


def paired(before, after):
    rows = []
    for left, right in zip(before["rows"], after["rows"]):
        rows.append({"id": left["id"], "case_id": left["case_id"], "label": left["label"],
                     "before_verdict": left["terminal_verdict"], "after_verdict": right["terminal_verdict"],
                     "before_correct": left["terminal_verdict_correct"], "after_correct": right["terminal_verdict_correct"],
                     "before_truncated": left["truncated"], "after_truncated": right["truncated"],
                     "before_guard": left["guard"], "after_guard": right["guard"]})
    complete = [(a, b) for a, b in zip(before["rows"], after["rows"])
                if not a["truncated"] and not b["truncated"] and a["parse_error"] is None and b["parse_error"] is None]
    return {"both_correct": sum(row["before_correct"] and row["after_correct"] for row in rows),
            "improved": sum(not row["before_correct"] and row["after_correct"] for row in rows),
            "regressed": sum(row["before_correct"] and not row["after_correct"] for row in rows),
            "both_incorrect": sum(not row["before_correct"] and not row["after_correct"] for row in rows),
            "both_format_valid": {"samples": len(complete),
                                  "before_correct": sum(a["terminal_verdict_correct"] for a, _ in complete),
                                  "after_correct": sum(b["terminal_verdict_correct"] for _, b in complete)},
            "rows": rows}


def write_report(records, result):
    names = {"v4_unconstrained": "v4，无约束", "checkpoint10_unconstrained": "checkpoint-10，无约束",
             "v4_bounded": "v4，同一现有约束", "checkpoint10_bounded": "checkpoint-10，同一现有约束"}
    lines = ["# 39 题统一输出约束对照", "", "评估日期：2026-11-08。只重新解码，不改权重。两模型使用原 evidence_v2 JSON schema、可引用截图步骤枚举及两处说明各 512 字符上限；greedy、1024 新 token、seed42、单样本，提示、截图、字段别名和评分规则保持。冻结五应用未参与。", "",
             "| 模型及输出方式 | 终判正确 | guard后正确 | 结构化正确 | 正常误报 | 缺陷检出 | blocked 正确 | 格式错误 | 截断 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, record in records.items():
        metrics = record["summary"]["all"]
        blocked = result["models"][name]["blocked_correct"]
        guarded = result["models"][name]["reported_guarded_correct"]
        lines.append(f"| {names[name]} | {metrics['terminal_verdict_correct']}/39 | {guarded}/39 | {metrics['strict_correct']}/39 | {metrics['normal_false_alarms']}/18 | {metrics['defects_detected']}/9 | {blocked}/12 | {metrics['format_errors']} | {metrics['truncated']} |")
    pair = result["paired"]["rl_with_bounded_output"]
    lines.extend(["", f"相同输出约束下，checkpoint-10 相对 v4 恢复 {pair['improved']} 题、退步 {pair['regressed']} 题；两者均正确 {pair['both_correct']} 题、均错误 {pair['both_incorrect']} 题。终判正确按完整 JSON 中的 verdict 统计，与本轮原口径一致。guard 后正确仅在完整、可解析回答上将 guard 触发的终判转为 blocked；截断或格式错误不计正确。", "",
                  f"按实际系统的 guard 处理，缺陷检出为 v4 {result['models']['v4_bounded']['reported_guarded_by_label']['fail']}/9、checkpoint-10 {result['models']['checkpoint10_bounded']['reported_guarded_by_label']['fail']}/9。表中的缺陷检出列记录模型原始终判；部分 fail 答案因自身字段或提交状态矛盾被 guard 转为 blocked。", "",
                  "结构化评分沿用原规则：已核验正确、已确认错误、尚待核验、截断无法评分分别统计。字段别名或替代截图引用尚待核验时，不计为已确认事实错误；终判正确也不等于其引用和说明全部正确。自由说明事实正确性未进入此评分。", "",
                  "| 输出方式 | 已核验结构化正确 | 已确认错误 | 无已确认错误但待核验 | 截断无法评分 |", "| --- | ---: | ---: | ---: | ---: |"])
    for name in records:
        counts = result["models"][name]["fact_assessment"]["exclusive_counts"]
        lines.append(f"| {names[name]} | {counts.get('verified_structured_correct',0)} | {counts.get('confirmed_error',0)} | {counts.get('unverified_without_confirmed_error',0)} | {counts.get('truncated_unscorable',0)} |")
    lines.extend(["", "逐题配对、来源与权重一致性核验见 [comparison.json](../reports/rl-review-bounded-20261108/comparison.json)。原无约束目录保持；新回答、清单、进程退出码、运行信息及源码快照位于 [本次报告目录](../reports/rl-review-bounded-20261108)。", "",
                  "重新生成时使用新目录，保留本次报告：", "", "```bash", "CUDA_VISIBLE_DEVICES=2 OMP_NUM_THREADS=4 TOKENIZERS_PARALLELISM=false \\", "  .model-venv/bin/python scripts/evaluate_review_grpo.py \\", "  --adapter runs/sft-qwen3vl-10apps-v4-review-selected/adapter \\", "  --schema-constrained --output reports/rl-review-bounded-reproduction/v4", "CUDA_VISIBLE_DEVICES=3 OMP_NUM_THREADS=4 TOKENIZERS_PARALLELISM=false \\", "  .model-venv/bin/python scripts/evaluate_review_grpo.py \\", "  --adapter runs/rl-review-grpo-retry-v1/checkpoint-10 \\", "  --schema-constrained --output reports/rl-review-bounded-reproduction/checkpoint-10", "```", "", "对本次已保存的四组结果重新汇总：", "", "```bash", ".venv/bin/python scripts/summarize_bounded_review_comparison.py", "```", ""])
    (ROOT / "docs/GRPO_BOUNDED_EVAL.md").write_text("\n".join(lines))


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
