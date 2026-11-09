"""Compare two completed 39-review evaluations under the same facts-only protocol."""

import argparse
import hashlib
import json
from pathlib import Path

from summarize_bounded_review_comparison import (
    fact_assessment, file_hash, guarded_verdict, load_result, paired, write_json,
)


ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports/rl-review-facts-20261108"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=REPORTS / "v4")
    parser.add_argument("--baseline-name", default="v4")
    parser.add_argument("--candidate", type=Path, default=REPORTS / "v5")
    parser.add_argument("--candidate-name", default="审查 SFT v5")
    parser.add_argument("--output", type=Path, default=REPORTS / "v4-v5")
    args = parser.parse_args()
    baseline, candidate = load_result(args.baseline), load_result(args.candidate)
    verify_sources(baseline, candidate)
    result = {
        "completed": True,
        "protocol": "evidence_facts_v1, greedy, schema constrained, 1024 tokens, seed42, original 39 development reviews",
        "holdout_used": False,
        "same_prompt_images_rules_code_and_generation": True,
        "baseline_name": args.baseline_name,
        "models": {
            args.baseline_name: model_result(baseline, args.baseline),
            args.candidate_name: model_result(candidate, args.candidate),
        },
        "paired": paired(baseline, candidate),
        "free_text_explanations": "Program-rendered from model facts; not model-generated.",
    }
    args.output.mkdir(parents=True, exist_ok=True)
    write_json(args.output / "comparison.json", result)
    write_report(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def verify_sources(before, after):
    left, right = before["manifest"], after["manifest"]
    common = ("dataset_sha256", "sample_sources", "config_sha256", "rules_sha256", "source_sha256",
              "generation", "prompt_transform")
    if any(left[name] != right[name] for name in common):
        raise ValueError("facts comparison changed prompts, images, data, rules, source or generation")
    left_config = {key: value for key, value in left["config"].items() if key != "adapter_path"}
    right_config = {key: value for key, value in right["config"].items() if key != "adapter_path"}
    if left_config != right_config:
        raise ValueError("facts comparison changed model configuration beyond the adapter")
    for record in (before, after):
        manifest = record["manifest"]
        if [row["id"] for row in record["rows"]] != [row["id"] for row in left["sample_sources"]]:
            raise ValueError("facts comparison changed sample IDs or order")
        for name, saved in manifest["adapter_sha256"].items():
            if file_hash(Path(manifest["adapter"]) / name) != saved:
                raise ValueError("evaluated adapter changed on disk")
        for name, saved in manifest["dataset_sha256"].items():
            if file_hash(Path(manifest["dataset"]) / name) != saved:
                raise ValueError("evaluated source data changed on disk")
        for row in record["rows"]:
            if row["raw_sha256"] != hashlib.sha256(row["raw"].encode()).hexdigest():
                raise ValueError("saved model completion hash differs")
    if left["generation"].get("model_generated_explanations") is not False:
        raise ValueError("facts comparison must use the model's facts-only protocol")


def model_result(record, path):
    rows = record["rows"]
    return {
        "path": str(path), "metrics": record["summary"]["all"],
        "blocked_correct": record["summary"]["label"]["blocked"]["terminal_verdict_correct"],
        "reported_guarded_correct": sum(guarded_verdict(row) == row["label"] for row in rows),
        "reported_guarded_by_label": {
            label: record["summary"]["label"][label]["reported_guarded_correct"]
            for label in ("pass", "fail", "blocked")},
        "fact_assessment": fact_assessment(rows),
        "mean_completion_tokens": sum(row["completion_tokens"] for row in rows) / len(rows),
        "max_completion_tokens": max(row["completion_tokens"] for row in rows),
        "adapter_sha256": record["manifest"]["adapter_sha256"],
    }


def write_report(output, result):
    lines = ["相同 facts-only 输出方式的 39 题开发集对照。模型只输出结构化事实，说明由程序生成。", "",
             "| 模型 | 终判正确 | guard后正确 | 结构化核验正确 | 缺陷检出 | 正常误报 | blocked正确 | 格式错误 | 截断 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, model in result["models"].items():
        m = model["metrics"]
        lines.append(f"| {name} | {m['terminal_verdict_correct']}/39 | {model['reported_guarded_correct']}/39 | {m['strict_correct']}/39 | {m['defects_detected']}/9 | {m['normal_false_alarms']}/18 | {model['blocked_correct']}/12 | {m['format_errors']} | {m['truncated']} |")
    pair = result["paired"]
    lines.extend(["", f"相对 {result['baseline_name']}，候选模型恢复 {pair['improved']} 题，退步 {pair['regressed']} 题；两者均正确 {pair['both_correct']} 题、均错误 {pair['both_incorrect']} 题。", "",
                  "guard 后正确仅对完整且可解析的回答应用证据一致性 guard，截断和格式错误不计正确。各标签实际通过 guard 的正确数：", "",
                  "| 模型 | 正常正确 | 缺陷正确 | blocked正确 |", "| --- | ---: | ---: | ---: |"])
    for name, model in result["models"].items():
        guarded = model["reported_guarded_by_label"]
        lines.append(f"| {name} | {guarded['pass']}/18 | {guarded['fail']}/9 | {guarded['blocked']}/12 |")
    lines.extend(["", "结构化核验正确要求结论、事实及已认可引用同时正确；其他回答中的已确认错误和待核验引用分开统计：", "",
                  "| 模型 | 已核验结构化正确 | 已确认错误 | 无已确认错误但待核验 | 截断无法评分 | 平均生成token |", "| --- | ---: | ---: | ---: | ---: | ---: |"])
    for name, model in result["models"].items():
        c = model["fact_assessment"]["exclusive_counts"]
        lines.append(f"| {name} | {c.get('verified_structured_correct',0)} | {c.get('confirmed_error',0)} | {c.get('unverified_without_confirmed_error',0)} | {c.get('truncated_unscorable',0)} | {model['mean_completion_tokens']:.1f} |")
    lines.extend(["", "两模型的39个ID、源数据哈希、转换后提示、原图、评分规则、推理源码与参数一致，只有adapter不同。冻结五应用保持留出。逐题配对与完整指标见 comparison.json。", ""])
    (output / "comparison.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
