"""Summarize complete, matched v3/v4 reviews and the recorded live UI runs."""

import argparse
import hashlib
import json
import os
import tarfile
from pathlib import Path

import yaml

from guiqa.review_contract import json_schema
from validate_review_protocol import file_hash, metrics


ROOT = Path(__file__).resolve().parents[1]
CONDITIONS = {
    "v3-old": (23, {"matched_validation": 23}, "evidence_v1"),
    "v3-new": (23, {"matched_validation": 23}, "evidence_v2"),
    "v3-curriculum": (2, {"new_curriculum": 2}, "evidence_v2"),
    "v4-new": (25, {"matched_validation": 23, "new_curriculum": 2}, "evidence_v2"),
}


def main():
    args = arguments()
    output = args.reports.resolve()
    groups = {name: load_group(output / name, *specification) for name, specification in CONDITIONS.items()}
    checks, protocol, archives = verify_comparison(groups)
    matched = {name: cohort(group, "matched_validation") for name, group in groups.items() if name != "v3-curriculum"}
    curriculum = {name: cohort(groups[name], "new_curriculum") for name in ("v3-curriculum", "v4-new")}
    pilot = read_pilot(output / "v3-new-unbounded-pilot")
    ui_paths = args.ui_results
    if ui_paths is None:
        ui_paths = [ROOT / f"reports/sft-v4-live/{name}/result.json"
                    for name in ("v3-lite", "v3-wolf", "v4-lite", "v4-wolf")
                    if (ROOT / f"reports/sft-v4-live/{name}/result.json").exists()]
    live = [read_live(path.resolve()) for path in ui_paths]
    comparison = {
        "scope": "training_application_review_only", "frozen_five_evaluated": False,
        "summarizer": artifact(Path(__file__)),
        "checks": checks, "new_protocol": protocol,
        "matched_validation": {name: metrics(rows) for name, rows in matched.items()},
        "new_curriculum": {name: metrics(rows) for name, rows in curriculum.items()},
        "conditions": {name: group["provenance"] for name, group in groups.items()},
        "matched_pairs": paired_rows(matched), "curriculum_pairs": paired_rows(curriculum),
        "unbounded_pilot": pilot, "live_ui": live, "source_archives": archives,
        "metric_definitions": {
            "accuracy": "final prediction equals the visible-evidence label; all planned rows remain in the denominator",
            "format_errors": "all non-null parser errors, including duplicate fields and invalid screenshot references",
            "guarded": "all non-null semantic guard errors",
            "confusion": "visible-evidence label -> final prediction",
            "field_value_exact": "literal equality for non-null labelled readings, including empty strings; protocols have different denominators",
        },
    }
    (output / "comparison.json").write_text(json.dumps(comparison, ensure_ascii=False, indent=2) + "\n")
    (output / "summary.md").write_text(render_summary(output, comparison))
    print(json.dumps({"comparison": str(output / "comparison.json"),
                      "summary": str(output / "summary.md"), "matched_validation": comparison["matched_validation"],
                      "new_curriculum": comparison["new_curriculum"], "live_ui_runs": len(live)}, ensure_ascii=False, indent=2))


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, default=ROOT / "reports/sft-v4-protocol")
    parser.add_argument("--ui-results", type=Path, action="append")
    return parser.parse_args()


def load_group(directory, expected_count, expected_cohorts, expected_format):
    manifest = read_json(directory / "manifest.json")
    summary = read_json(directory / "summary.json")
    rows = read_rows(directory / "predictions.jsonl")
    identifiers = [row["id"] for row in rows]
    if (len(identifiers) != expected_count or len(set(identifiers)) != len(identifiers)
            or len(manifest["sample_ids"]) != expected_count or set(identifiers) != set(manifest["sample_ids"])
            or manifest["samples"] != expected_count or summary["planned"] != expected_count or not summary["completed"]):
        raise ValueError(f"{directory.name}: validation must contain every planned row exactly once")
    if summary["metrics"] != metrics(rows):
        raise ValueError(f"{directory.name}: summary metrics differ from saved predictions")
    counts = {name: len([row for row in rows if row["cohort"] == name]) for name in {row["cohort"] for row in rows}}
    if counts != expected_cohorts or any(row["review_format"] != expected_format for row in rows):
        raise ValueError(f"{directory.name}: unexpected validation cohort or review format")
    split_path = Path(manifest["dataset"]) / f"{manifest['partition']}.jsonl"
    if file_hash(split_path) != manifest["split_sha256"]:
        raise ValueError(f"{directory.name}: source split changed")
    source_rows = {row["id"]: row for row in read_rows(split_path) if row["phase"] == "review"}
    split = yaml.safe_load((ROOT / "configs/sft-split.yaml").read_text())
    training_cases = set(split["training_pilot"] + split["training_supplementary"])
    for row in rows:
        source = source_rows[row["id"]]
        label = json.loads(source["messages"][-1]["content"])["verdict"]
        messages = source["messages"][:-1]
        prompt_hash = hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if (row["case_id"] not in training_cases or source["data_split"] != "validation"
                or row["source_run_id"] != source["derived_provenance"]["source_run_id"]
                or row["label"] != label or row["adapter_sha256"] != manifest["adapter_sha256"]
                or row["prompt_sha256"] != prompt_hash):
            raise ValueError(f"{directory.name}: prediction provenance differs from its validation sample: {row['id']}")
        supplied = [Path(manifest["dataset"]) / item["image"] for message in messages
                    for item in message["content"] if item["type"] == "image"]
        images = row["source_image_hashes"]
        if len(supplied) != len(images):
            raise ValueError(f"{directory.name}: supplied image count differs: {row['id']}")
        for path, recorded in zip(supplied, images):
            if path.resolve() != Path(recorded["path"]).resolve() or file_hash(path) != recorded["sha256"]:
                raise ValueError(f"{directory.name}: source image differs: {path}")
    provenance = {"manifest": artifact(directory / "manifest.json"),
                  "predictions": artifact(directory / "predictions.jsonl"),
                  "summary": artifact(directory / "summary.json"),
                  "dataset_split": artifact(split_path), "adapter_sha256": manifest["adapter_sha256"],
                  "config": manifest["config"], "config_sha256": manifest["config_sha256"],
                  "code_sha256": manifest["code_sha256"], "generation": manifest["generation"],
                  "lm_format_enforcer": manifest["lm_format_enforcer"],
                  "metrics": metrics(rows), "cohorts": {name: metrics(cohort({"rows": rows}, name)) for name in counts}}
    return {"manifest": manifest, "rows": rows, "provenance": provenance}


def verify_comparison(groups):
    old, new, curriculum, v4 = (groups[name]["manifest"] for name in CONDITIONS)
    if len({old["adapter_sha256"], new["adapter_sha256"], curriculum["adapter_sha256"]}) != 1:
        raise ValueError("all three v3 conditions must use exactly the same adapter")
    if v4["adapter_sha256"] == new["adapter_sha256"]:
        raise ValueError("v4 validation must use its separately trained adapter")
    for manifest in (old, v4):
        weights = Path(manifest["config"]["adapter_path"]) / "adapter_model.safetensors"
        if file_hash(weights) != manifest["adapter_sha256"]:
            raise ValueError("saved adapter weights differ from the evaluated adapter hash")
    for manifest in (curriculum, v4):
        if (manifest["code_sha256"] != new["code_sha256"] or manifest["generation"] != new["generation"]
                or manifest["lm_format_enforcer"] != new["lm_format_enforcer"]):
            raise ValueError("v3/v4 new-protocol inference code and generation settings must be identical")
    common_config = {key: value for key, value in new["config"].items() if key != "adapter_path"}
    if any({key: value for key, value in manifest["config"].items() if key != "adapter_path"} != common_config
           for manifest in (curriculum, v4)):
        raise ValueError("v3/v4 new-protocol configuration differs beyond adapter_path")
    match = [cohort(groups[name], "matched_validation") for name in ("v3-old", "v3-new", "v4-new")]
    verify_pairs(match, compare_prompts=False)
    verify_pairs(match[1:], compare_prompts=True)
    verify_pairs([cohort(groups[name], "new_curriculum") for name in ("v3-curriculum", "v4-new")], compare_prompts=True)
    archive_paths = [ROOT / "runs/sft-qwen3vl-10apps-v4/training-source.tar.gz",
                     ROOT / "runs/sft-qwen3vl-10apps-v4/training-source.json",
                     ROOT / "runs/sft-v4-protocol-workers/bounded-inference-source.tar.gz",
                     ROOT / "runs/sft-v4-protocol-workers/bounded-inference-fix.json"]
    archived = {}
    for path in (archive_paths[0], archive_paths[2]):
        with tarfile.open(path, "r:gz") as archive:
            for name in new["code_sha256"]:
                if name in archive.getnames():
                    archived[name] = hashlib.sha256(archive.extractfile(name).read()).hexdigest()
    if archived != new["code_sha256"]:
        raise ValueError("saved inference code archives do not reproduce the new-protocol code hashes")
    contract = ROOT / "src/guiqa/review_contract.py"
    if file_hash(contract) != new["code_sha256"]["src/guiqa/review_contract.py"]:
        raise ValueError("active review contract differs from the evaluated archived contract")
    schema = json_schema([0])
    observation_limits = [schema["properties"]["observation"]["maxLength"],
                          schema["properties"]["submission"]["properties"]["evidence"]["anyOf"][1]
                          ["properties"]["observation"]["maxLength"]]
    fix = read_json(archive_paths[3])
    if observation_limits != [512, 512] or fix["observation_max_chars"] != 512:
        raise ValueError("new-protocol observation limits must both be 512 characters")
    protocol = {"review_format": "evidence_v2", "observation_max_chars": 512,
                "schema_step_enum_placeholder": [0], "schema_template": schema,
                "schema_template_sha256": hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest(),
                "code_sha256": new["code_sha256"], "generation": new["generation"],
                "bounded_fix": artifact(archive_paths[3]),
                "training_prompt_and_dataset_unchanged": fix["training_prompt_and_dataset_unchanged"]}
    checks = {"complete_unique_conditions": True, "matched_23_source_runs_and_labels": True,
              "curriculum_2_source_runs_and_labels": True, "fixed_v3_adapter": True,
              "separate_v4_adapter": True, "identical_new_protocol_code_and_generation": True,
              "adapter_files_match_evaluated_hashes": True,
              "identical_new_protocol_prompts_and_images": True, "observation_limits_512": True,
              "archived_code_matches": True, "source_split_prompt_and_image_hashes_match": True,
              "training_applications_only": True}
    return checks, protocol, [artifact(path) for path in archive_paths]


def verify_pairs(cohorts, compare_prompts):
    indexed = [{row["source_run_id"]: row for row in rows} for rows in cohorts]
    if any(len(index) != len(rows) for index, rows in zip(indexed, cohorts)):
        raise ValueError("a comparison cohort contains duplicate source_run_id")
    reference = indexed[0]
    for other in indexed[1:]:
        if set(other) != set(reference):
            raise ValueError("comparison cohorts have missing or different source runs")
        for run_id, row in reference.items():
            candidate = other[run_id]
            if (row["label"], row["case_id"]) != (candidate["label"], candidate["case_id"]):
                raise ValueError(f"comparison label or application differs for {run_id}")
            if compare_prompts and (row["prompt_sha256"] != candidate["prompt_sha256"]
                    or [image["sha256"] for image in row["source_image_hashes"]]
                    != [image["sha256"] for image in candidate["source_image_hashes"]]):
                raise ValueError(f"new-protocol prompt or images differ for {run_id}")


def cohort(group, name):
    return [row for row in group["rows"] if row["cohort"] == name]


def paired_rows(groups):
    pairs = {}
    for name, rows in groups.items():
        for row in rows:
            pair = pairs.setdefault(row["source_run_id"], {"source_run_id": row["source_run_id"],
                                                           "case_id": row["case_id"], "label": row["label"], "conditions": {}})
            pair["conditions"][name] = {key: row[key] for key in
                                         ("id", "prediction", "model_verdict", "parse_error", "guard", "prompt_sha256",
                                          "source_image_hashes", "field_exact_matches", "adapter_sha256")}
    return list(pairs.values())


def read_pilot(directory):
    manifest = read_json(directory / "manifest.json")
    rows = read_rows(directory / "predictions.jsonl")
    fix = read_json(ROOT / "runs/sft-v4-protocol-workers/bounded-inference-fix.json")
    return {"included_in_main_comparison": False, "completed_predictions": len(rows), "planned": manifest["samples"],
            "metrics": metrics(rows), "reason": fix["reason"],
            "manifest": artifact(directory / "manifest.json"), "predictions": artifact(directory / "predictions.jsonl"),
            "summary": artifact(directory / "summary.json")}


def read_live(path):
    saved = read_json(path)
    result = saved["result"]
    return {"source": artifact(path), "scenario_id": saved["scenario_id"], "run_path": saved["run_path"],
            "agent_verdict": result["agent_verdict"], "oracle_verdict": result["oracle_verdict"],
            "expected": result["matrix_expected"], "passed": saved["passed"],
            "oracle_matches_expected": saved["oracle_matches_expected"],
            "agent_matches_oracle": saved["agent_matches_oracle"],
            "termination_reason": result["termination_reason"], "config": saved["config"],
            "catalog_sha256": saved["catalog_sha256"], "script_sha256": saved["script_sha256"]}


def render_summary(output, comparison):
    matched, curriculum = comparison["matched_validation"], comparison["new_curriculum"]
    lines = ["# v4训练应用审查验证", "",
             "本报告使用训练应用验证集的固定截图与动作历史，衡量审查阶段。五个冻结应用的端到端成绩另行评估。", "",
             "23条原验证样本逐条匹配运行和标签；v3旧、新协议使用同一adapter，v3/v4新协议使用相同代码、512字符说明上限、生成参数、提示词和图片。", "",
             "| 指标 | v3旧协议 | v3新协议 | v4新协议 |", "|---|---:|---:|---:|"]
    append_metrics(lines, [matched[name] for name in ("v3-old", "v3-new", "v4-new")])
    lines.extend(["", "| 新增2条验证样本 | v3新协议 | v4新协议 |", "|---|---:|---:|"])
    append_metrics(lines, [curriculum[name] for name in ("v3-curriculum", "v4-new")])
    lines.extend(["", "格式拒绝统计所有解析错误，包含重复字段、引用步骤不合法；被格式或证据要求挡住的预测按blocked保留在正确率分母中。",
                  "字段原文完全一致按非null标注计数，包含可见空串。新协议增加基准和字段，其分母与旧协议不同，应分别阅读这些数量。", "",
                  "| 标签→预测 | v3旧协议 | v3新协议 | v4新协议 |", "|---|---:|---:|---:|"])
    for label in ("pass", "fail", "blocked"):
        for prediction in ("pass", "fail", "blocked"):
            key = f"{label}->{prediction}"
            values = [matched[name]["confusion"].get(key, 0) for name in ("v3-old", "v3-new", "v4-new")]
            lines.append(f"| {label}→{prediction} | " + " | ".join(map(str, values)) + " |")
    pilot = comparison["unbounded_pilot"]
    lines.extend(["", f"无说明长度上限的初次试跑实际完成{pilot['completed_predictions']}/{pilot['planned']}条；"
                  "观察到说明文字重复耗尽生成预算后停止，并加入512字符上限。该试跑独立保留，主比较使用完整有上限运行。"
                  f"[原输出]({relative(pilot['predictions']['path'], output)})。", "", "## 实际UI运行", ""])
    if comparison["live_ui"]:
        lines.extend(["| 运行 | 场景 | Agent | Oracle | 预期 | 两项均符合 |", "|---|---|---|---|---|---|"])
        for row in comparison["live_ui"]:
            name = Path(row["source"]["path"]).parent.name
            lines.append(f"| [{name}]({relative(row['source']['path'], output)}) | {row['scenario_id']} | "
                         f"{row['agent_verdict']} | {row['oracle_verdict']} | {row['expected']} | {row['passed']} |")
        lines.append("")
        lines.extend(f"- [{Path(row['source']['path']).parent.name}实际轨迹]({relative(row['run_path'], output)})："
                     f"`{row['termination_reason']}`。" for row in comparison["live_ui"])
    else:
        lines.append("本次汇总未提供实际UI运行结果。")
    lines.extend(["", "## 来源", "",
                  "[完整比较与逐样本prompt、图片哈希](comparison.json)记录所有代码、权重、输入和输出来源。",
                  f"[汇总脚本]({relative(comparison['summarizer']['path'], output)})的SHA256为 "
                  f"`{comparison['summarizer']['sha256']}`。"])
    for name, provenance in comparison["conditions"].items():
        lines.append(f"- {name}：[manifest]({relative(provenance['manifest']['path'], output)})、"
                     f"[原始预测]({relative(provenance['predictions']['path'], output)})、"
                     f"[指标]({relative(provenance['summary']['path'], output)})。")
    for item in comparison["source_archives"]:
        lines.append(f"- [{Path(item['path']).name}]({relative(item['path'], output)})，SHA256 `{item['sha256']}`。")
    return "\n".join(lines) + "\n"


def append_metrics(lines, values):
    exact = lambda value: f"{value['field_value_exact']['correct']}/{value['field_value_exact']['total']}"
    specs = [("正确判定", lambda value: f"{value['correct']}/{value['samples']}"),
             ("正确率", lambda value: f"{value['accuracy']:.1%}"),
             ("格式拒绝", lambda value: value["format_errors"]),
             ("证据要求拦截", lambda value: value["guarded"]),
             ("fail误判pass", lambda value: value["false_pass"]),
             ("pass误判fail", lambda value: value["false_fail"]),
             ("blocked标签判pass", lambda value: value["pass_on_blocked_label"]),
             ("字段原文完全一致", exact)]
    for label, render in specs:
        lines.append(f"| {label} | " + " | ".join(str(render(value)) for value in values) + " |")


def artifact(path):
    return {"path": str(path.resolve().relative_to(ROOT)), "sha256": file_hash(path)}


def relative(path, directory):
    return Path(os.path.relpath(ROOT / path, directory)).as_posix()


def read_json(path):
    return json.loads(path.read_text())


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


if __name__ == "__main__":
    main()
