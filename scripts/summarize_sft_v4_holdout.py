"""Summarize four shards that re-review the forty original v3 holdout trajectories."""

import argparse
import json
import os
from collections import Counter
from pathlib import Path

import yaml

from guiqa.artifacts import write_json
from guiqa.sft_v2 import checksum, read_json, read_rows, value_hash


CASES = {
    "sweeper-005": "Akaunting", "sweeper-007": "Ampache", "sweeper-008": "Anchor CMS",
    "sweeper-040": "EasyAppointments", "sweeper-041": "emlog",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("reports/sft-v4-review-holdout"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    directory = (root / args.directory).resolve()
    rows, manifest, completion, verification = load_results(root, directory)
    original = metrics(rows, "source_agent_verdict")
    reviewed = metrics(rows, "new_verdict")
    recovered = [row for row in rows if row["recovered"]]
    regressed = [row for row in rows if row["regressed"]]
    cases = {}
    for case in CASES:
        selected = [row for row in rows if row["case_id"] == case]
        cases[case] = {
            "app": CASES[case], "original": metrics(selected, "source_agent_verdict"),
            "reviewed": metrics(selected, "new_verdict"),
            "recovered_original_blocked": sum(row["recovered"] for row in selected),
            "regressed_original_correct": sum(row["regressed"] for row in selected),
        }
    blocked_groups = {}
    for group in ("schema_parse_failure", "other_review_blocked"):
        selected = [row for row in rows if row["source_group"] == "original_blocked"
                    and (row["source_error_kind"] == "schema_parse_failure") == (group == "schema_parse_failure")]
        blocked_groups[group] = {
            "original_count": len(selected), "reviewed": metrics(selected, "new_verdict"),
            "recovered": sum(row["recovered"] for row in selected),
            "source_error_kinds": dict(Counter(row["source_error_kind"] for row in selected)),
            "source_run_ids": [row["source_run_id"] for row in selected],
            "recovered_run_ids": [row["source_run_id"] for row in selected if row["recovered"]],
        }
    references = {
        "method": "docs/SFT_V4.md", "training_completion": manifest["training_completion_path"],
        "training_source": "runs/sft-qwen3vl-10apps-v4/training-source.json",
        "training_source_archive": "runs/sft-qwen3vl-10apps-v4/training-source.tar.gz",
        "bounded_inference": "runs/sft-v4-protocol-workers/bounded-inference-fix.json",
        "bounded_inference_archive": "runs/sft-v4-protocol-workers/bounded-inference-source.tar.gz",
    }
    if completion["selection_metric"] == "training_app_review_correctness":
        references["checkpoint_selection"] = str(Path(manifest["training_completion_path"]).parent / "selection.json")
        references["checkpoint_selection_source"] = str(Path(manifest["training_completion_path"]).parent / "selection-source.py")
        selection_text = ("checkpoint 仅按训练应用25条 dev 的审查正确数选择；并列时依次比较非 pass "
                          "被误判为 pass 的次数及完整验证集 eval_loss。")
    else:
        selection_text = "checkpoint 仅按训练应用验证集最低 eval_loss 选择。"
    summary = {
        "completed": True, "evaluation_type": "original_trajectory_re_review", "trajectories": 40,
        "operations_replayed": False, "new_end_to_end_runs": 0, "training_rows_generated": 0,
        "original": original, "reviewed": reviewed,
        "parse_errors": sum(row["parse_error"] is not None for row in rows),
        "guarded": sum(row["guard"] is not None for row in rows),
        "new_error_kinds": dict(Counter(row["error_kind"] for row in rows)),
        "recovered_original_blocked": len(recovered), "original_blocked": 14,
        "regressed_original_correct": len(regressed), "original_correct": 26,
        "cases": cases, "original_blocked_groups": blocked_groups,
        "recovered_run_ids": [row["source_run_id"] for row in recovered],
        "regressions": [{key: row[key] for key in (
            "source_run_id", "source_path", "case_id", "source_agent_verdict", "source_oracle_verdict",
            "new_verdict", "error_kind", "parse_error", "guard")} for row in regressed],
        "checkpoint": {key: completion[key] for key in (
            "best_checkpoint", "selected_epoch", "selection_metric", "validation_metrics", "validation_case_ids")},
        "review_checkpoint_selection": completion.get("review_checkpoint_selection"),
        "verification": verification,
        "provenance": {key: manifest[key] for key in (
            "adapter_sha256", "config_path", "config_sha256", "generation", "source_data_sha256",
            "source_code_sha256", "training_completion_sha256", "checkpoint_selection")},
        "references": references,
        "reference_sha256": {name: checksum(root / path) for name, path in references.items()},
        "metric_definitions": {
            "correct": "new verdict equals the original independent oracle, including all forty trajectories",
            "defect_recall": "new fail on the ten oracle-expected fail trajectories, divided by ten",
            "normal_false_fail": "new fail on the thirty oracle-expected pass trajectories",
            "blocked": "blocked, parse-error and guarded outputs remain in the denominator",
            "recovered": "an original blocked verdict becomes the oracle-expected pass or fail",
            "regressed": "an original correct verdict becomes an incorrect or blocked verdict",
        },
    }
    (directory / "results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    write_json(directory / "summary.json", summary)
    lines = [
        "# SFT v4：原轨迹重审", "",
        "本报告重新审查 v3 在冻结五应用上已完成的40条原轨迹。操作与独立 oracle 沿用 v3 原运行；"
        "没有重新执行操作。这里报告的是原轨迹重审结果，不能作为新40次端到端评估成绩。",
        "", selection_text + f" 所选 epoch {completion['selected_epoch']}，"
        f"`{completion['best_checkpoint']}`。冻结五应用的重审结果未参与选择。", "",
        "| 指标 | v3 原结果 | v4 原轨迹重审 |", "| --- | ---: | ---: |",
        f"| 正确判定 | {original['correct']}/40 | {reviewed['correct']}/40 |",
        f"| 缺陷检出 / 召回 | {original['defect_detected']}/10 | {reviewed['defect_detected']}/10 |",
        f"| 正常误报 | {original['normal_false_fail']}/30 | {reviewed['normal_false_fail']}/30 |",
        f"| blocked | {original['blocked']}/40 | {reviewed['blocked']}/40 |", "",
        f"新审查解析错误 {summary['parse_errors']}/40，guard 挡住 {summary['guarded']}/40。"
        f"原14条 blocked 恢复 {len(recovered)}/14；原26条正确结果退步 {len(regressed)}/26。"
        "全部40条均保留，解析错误、guard 与 blocked 计入分母。", "",
        "| 应用 | 原正确 | 重审正确 | 缺陷检出 | 正常误报 | blocked | 恢复 | 退步 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for case, item in cases.items():
        value = item["reviewed"]
        lines.append(
            f"| {item['app']} ({case}) | {item['original']['correct']}/8 | {value['correct']}/8 | "
            f"{value['defect_detected']}/{value['defect_total']} | "
            f"{value['normal_false_fail']}/{value['normal_total']} | {value['blocked']}/8 | "
            f"{item['recovered_original_blocked']} | {item['regressed_original_correct']} |")
    lines.extend(["", "| 原 blocked 原因 | 原条数 | 恢复正确 | 仍 blocked | 重审错误判定 |",
                  "| --- | ---: | ---: | ---: | ---: |"])
    for group, label in (("schema_parse_failure", "格式拒绝"), ("other_review_blocked", "其余证据判断")):
        item = blocked_groups[group]
        value = item["reviewed"]
        lines.append(f"| {label} | {item['original_count']} | {item['recovered']} | {value['blocked']} | "
                     f"{value['samples'] - value['correct'] - value['blocked']} |")
    lines.extend(["", "退步轨迹：", ""])
    if regressed:
        for row in regressed:
            path = os.path.relpath(root / row["source_path"] / "result.json", directory)
            lines.append(f"- [{row['source_run_id']}]({path})：{row['case_id']}，"
                         f"原 {row['source_agent_verdict']} → 重审 {row['new_verdict']}，"
                         f"oracle {row['source_oracle_verdict']}，原因 `{row['error_kind']}`。")
    else:
        lines.append("无。")
    lines.extend(["", "4个分片均完成；40条源轨迹及动作保持不变，原始文件与所用截图哈希复核通过。"
                  "四份 adapter、配置、生成参数、源数据、推理代码与 checkpoint 选择记录一致。", "",
                  "完整结果见 [results.jsonl](results.jsonl)，数值与来源哈希见 [summary.json](summary.json)。", "",
                  "来源："])
    for name, path in references.items():
        lines.append(f"- [{name}]({os.path.relpath(root / path, directory)})")
    (directory / "summary.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"completed": True, "original_correct": original["correct"],
                      "reviewed_correct": reviewed["correct"], "recovered": len(recovered),
                      "regressed": len(regressed), "directory": str(directory)}, ensure_ascii=False))


def load_results(root, directory):
    manifests, rows, shard_hashes = [], [], {}
    for index in range(4):
        shard = directory / f"shard-{index}"
        manifest, summary = read_json(shard / "manifest.json"), read_json(shard / "summary.json")
        selected = read_rows(shard / "results.jsonl")
        planned = manifest["planned_run_ids"]
        if manifest["shard_index"] != index or manifest["shards"] != 4:
            raise ValueError(f"{shard.name}: shard identity differs from its directory")
        if (summary["completed"] is not True or summary["planned"] != len(planned)
                or summary["reviewed"] != len(selected) or len(selected) != len(planned)
                or [row["source_run_id"] for row in selected] != planned):
            raise ValueError(f"{shard.name}: every planned review must finish and remain in results")
        manifests.append({key: value for key, value in manifest.items()
                          if key not in {"shard_index", "planned_run_ids"}})
        rows.extend(selected)
        shard_hashes[shard.name] = {name: checksum(shard / name)
                                    for name in ("manifest.json", "summary.json", "results.jsonl")}
    manifest = manifests[0]
    if any(item != manifest for item in manifests[1:]):
        raise ValueError("the four shards used different source, adapter, configuration, generation or code inputs")
    frozen = manifest["source_runs"]
    identities = [row["source_run_id"] for row in frozen]
    if (manifest["source_total"] != 40 or len(frozen) != 40 or len(set(identities)) != 40
            or len(rows) != 40 or len({row["source_run_id"] for row in rows}) != 40
            or {row["source_run_id"] for row in rows} != set(identities)):
        raise ValueError("re-review requires all forty distinct original trajectories")
    if value_hash(frozen) != manifest["source_data_sha256"]:
        raise ValueError("source data differs from its frozen hash")
    source_map = {row["source_run_id"]: row for row in frozen}
    artifact_hashes = dict(manifest["source_completed_files"])
    for row in rows:
        source = source_map[row["source_run_id"]]
        if any(row[key] != value for key, value in source.items()):
            raise ValueError(f"{row['source_run_id']}: result differs from its frozen source")
        expected, original = row["source_oracle_verdict"], row["source_agent_verdict"]
        if expected not in {"pass", "fail"} or row["matrix_expected"] != expected:
            raise ValueError("source independent oracle must match its expected outcome")
        if ((row["source_group"] == "original_correct" and original != expected)
                or (row["source_group"] == "original_blocked" and original != "blocked")
                or row["source_group"] not in {"original_correct", "original_blocked"}):
            raise ValueError("original correct and blocked groups must reflect the actual source verdict")
        correct = row["new_verdict"] == expected
        if (row["new_verdict"] not in {"pass", "fail", "blocked"} or row["correct"] is not correct
                or row["recovered"] is not (row["source_group"] == "original_blocked" and correct)
                or row["regressed"] is not (row["source_group"] == "original_correct" and not correct)):
            raise ValueError("reported correctness, recovery or regression differs from the verdict")
        if ((row["parse_error"] is not None or row["guard"] is not None) and row["new_verdict"] != "blocked"):
            raise ValueError("parse errors and guarded reviews must remain blocked")
        if (row["source_artifacts_unchanged"] is not True or row["actions_unchanged"] is not True
                or row["operations_replayed"] is not False):
            raise ValueError("source artifacts and actions must remain unchanged without replay")
        artifact_hashes.update(row["source_hashes"])
        artifact_hashes.update(row["screenshot_hashes"])
    case_counts = Counter(row["case_id"] for row in rows)
    expected_counts = Counter(row["matrix_expected"] for row in rows)
    group_counts = Counter(row["source_group"] for row in rows)
    schema_blocked = sum(row["source_group"] == "original_blocked"
                         and row["source_error_kind"] == "schema_parse_failure" for row in rows)
    if (case_counts != {case: 8 for case in CASES} or expected_counts != {"fail": 10, "pass": 30}
            or group_counts != {"original_blocked": 14, "original_correct": 26}
            or manifest["source_group_counts"] != group_counts or schema_blocked != 8):
        raise ValueError("source coverage must be five apps by eight, ten defects, thirty normal, fourteen blocked and twenty-six correct")
    if (manifest["purpose"] != "holdout_review_only" or manifest["operations_replayed"] is not False
            or manifest["training_rows_generated"] != 0):
        raise ValueError("this aggregation is review-only and generates no training data")
    for path, expected_hash in {**artifact_hashes, **manifest["source_code_sha256"]}.items():
        if checksum(root / path) != expected_hash:
            raise ValueError(f"a source artifact or inference input changed: {path}")
    completion_path = root / manifest["training_completion_path"]
    completion = read_json(completion_path)
    split = yaml.safe_load((root / "configs/sft-split.yaml").read_text())
    training_cases = set(split["training_pilot"] + split["training_supplementary"])
    if (completion["completed"] is not True or checksum(completion_path) != manifest["training_completion_sha256"]
            or completion["adapter_sha256"] != manifest["adapter_sha256"]
            or completion["selected_epoch"] != manifest["selected_epoch"]
            or not set(completion["validation_case_ids"])
            or not set(completion["validation_case_ids"]) <= training_cases):
        raise ValueError("checkpoint must match its completed training-application validation selection")
    if completion["selection_metric"] == "eval_loss":
        best = min(completion["epoch_validation"], key=lambda item: item["eval_loss"])
        if (completion["selected_epoch"] != best["epoch"]
                or Path(completion["best_checkpoint"]).name != Path(best["checkpoint"]).name):
            raise ValueError("loss-selected checkpoint differs from the training-validation minimum")
    elif completion["selection_metric"] == "training_app_review_correctness":
        from select_sft_v4_review_checkpoint import SELECTION_RULES, choose_checkpoint

        selection = completion["review_checkpoint_selection"]
        source = completion["source_training_completion"]
        chosen, recomputed, original = choose_checkpoint(
            root, Path(selection["development_reports"]["epoch1"]),
            Path(selection["development_reports"]["epoch2"]), Path(source["path"]))
        if (selection != recomputed or completion["selection_rules"] != SELECTION_RULES
                or source["sha256"] != recomputed["source_training_completion"]["sha256"]
                or completion["selected_epoch"] != chosen["epoch"]
                or completion["best_checkpoint"] != chosen["checkpoint"]
                or completion["adapter_sha256"] != chosen["adapter_sha256"]
                or completion["validation_metrics"] != {"eval_loss": chosen["full_validation_loss"], "epoch": chosen["epoch"]}
                or any(completion[key] != original[key] for key in
                       ("epoch", "optimizer_steps", "epoch_validation", "data_sha256", "validation_case_ids"))):
            raise ValueError("review-selected checkpoint differs from the recorded training-development rule")
    else:
        raise ValueError("unsupported training checkpoint selection metric")
    adapter = Path(manifest["config"]["adapter_path"])
    if (checksum(root / adapter / "adapter_model.safetensors") != manifest["adapter_sha256"]
            or checksum(Path(manifest["config_path"])) != manifest["config_sha256"]):
        raise ValueError("adapter or configuration changed after re-review")
    by_id = {row["source_run_id"]: row for row in rows}
    ordered = [by_id[identifier] for identifier in identities]
    verification = {
        "completed_shards": 4, "distinct_trajectories": 40, "case_counts": dict(case_counts),
        "oracle_expected_counts": dict(expected_counts), "source_group_counts": dict(group_counts),
        "original_correct_verdicts_verified": True, "all_failed_reviews_retained": True,
        "source_artifacts_unchanged": True, "actions_unchanged": True, "operations_replayed": False,
        "source_artifacts_rehashed": len(artifact_hashes), "shard_inputs_identical": True,
        "checkpoint_selected_on_training_validation_only": True, "shard_sha256": shard_hashes,
        "checkpoint_selection_metric": completion["selection_metric"],
    }
    return ordered, manifest, completion, verification


def metrics(rows, verdict_key):
    defects = [row for row in rows if row["source_oracle_verdict"] == "fail"]
    normal = [row for row in rows if row["source_oracle_verdict"] == "pass"]
    correct = sum(row[verdict_key] == row["source_oracle_verdict"] for row in rows)
    detected = sum(row[verdict_key] == "fail" for row in defects)
    false_fail = sum(row[verdict_key] == "fail" for row in normal)
    return {
        "samples": len(rows), "correct": correct, "accuracy": correct / len(rows) if rows else None,
        "defect_detected": detected, "defect_total": len(defects),
        "defect_recall": detected / len(defects) if defects else None,
        "normal_false_fail": false_fail, "normal_total": len(normal),
        "normal_false_fail_rate": false_fail / len(normal) if normal else None,
        "blocked": sum(row[verdict_key] == "blocked" for row in rows),
    }


if __name__ == "__main__":
    main()
