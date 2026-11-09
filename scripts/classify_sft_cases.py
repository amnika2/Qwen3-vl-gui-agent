"""Render reviewed workflow classes and the ten-app training selection."""
import collections
import csv
import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def main():
    cases, review, split, readiness = load_inputs(ROOT)
    report = build_report(cases, review, split, readiness)
    output = ROOT / "reports"
    (output / "sft-classification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    with (output / "sft-classification.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "case_id", "slug", "cohort", "workflow", "workflow_id", "type", "label", "purpose"])
        writer.writeheader()
        for case in report["cases"]:
            for flow in case["workflows"]:
                writer.writerow({**{key: case[key] for key in ("case_id", "slug", "cohort")}, **flow})
    lines = ["# SFT workflow 分类与训练选择", "",
             "由 scripts/classify_sft_cases.py 生成；修改 configs/sft-case-types.yaml 后重新运行。", "",
             f"官方案例 {len(cases)}；官方 workflow {len(cases) * 2}；主训练应用 {len(report['main_training'])}；计划训练 workflow {report['planned_training_workflows']}。", "",
             "## 官方 target 分类", "", "| 类型 | 含义 | 应用数 |", "|---|---|---:|"]
    for code, label in review["categories"].items():
        lines.append(f"| {code} | {label} | {report['target_counts'].get(code, 0)} |")
    lines += ["", "## 主训练应用", "", "| case | 应用 | 来源 | 训练任务 | 当前状态 |", "|---|---|---|---|---|"]
    for case in report["main_training"]:
        lines.append(f"| {case['case_id']} | {case['slug']} | {case['source']} | {case['task']} | {case['status']} |")
    lines += ["", "## 官方任务逐项分类", "", "| case | 应用 | target | preservation | 分组 |", "|---|---|---|---|---|"]
    for case in report["cases"]:
        types = {flow["workflow"]: flow["type"] for flow in case["workflows"]}
        lines.append(f"| {case['case_id']} | {case['slug']} | {types['target']} | {types['preservation']} | {case['cohort']} |")
    lines += ["", "DokuWiki、FlatPress 的官方任务仍按 N 分类；主训练使用另列的自建 F 流程。",
              "主训练是应用选择名单，不代表所有应用已部署或已导出数据。", ""]
    (output / "sft-classification.txt").write_text("\n".join(lines))
    print(json.dumps({key: report[key] for key in (
        "official_cases", "official_workflows", "target_counts", "preservation_counts")}, ensure_ascii=False))
    print("main_training=" + ",".join(case["case_id"] for case in report["main_training"]))


def load_inputs(root):
    raw = (root / "vendor/SWEeper-Bench/test.jsonl").read_bytes()
    source_manifest = json.loads((root / "docs/data-manifest.json").read_text())
    if hashlib.sha256(raw).hexdigest() != source_manifest["sha256"]:
        raise ValueError("benchmark bytes differ from the pinned source manifest")
    cases = [json.loads(line) for line in raw.splitlines() if line.strip()]
    review = yaml.safe_load((root / "configs/sft-case-types.yaml").read_text())
    split = yaml.safe_load((root / "configs/sft-split.yaml").read_text())
    readiness = yaml.safe_load((root / "configs/sft-app-readiness.yaml").read_text())["apps"]
    ids = {case["case_id"] for case in cases}
    if set(review["case_types"]) != ids:
        raise ValueError("reviewed classifications must cover every benchmark case exactly")
    for case_id, codes in review["case_types"].items():
        if len(codes) != 2 or set(codes) - set(review["categories"]):
            raise ValueError(f"{case_id}: expected reviewed target and preservation type codes")
    main = split["training_pilot"] + split["training_supplementary"]
    if len(main) != 10 or len(set(main)) != 10 or set(main) != set(readiness):
        raise ValueError("main training and readiness must name the same ten distinct apps")
    groups = [split[key] for key in ("eval_holdout", "training_pilot", "training_supplementary",
                                     "training_pool", "auxiliary_pool", "deferred")]
    members = [case for group in groups for case in group]
    if len(members) != len(set(members)) or set(members) - ids:
        raise ValueError("split groups must be disjoint and contain known cases")
    for case in split["training_pilot"]:
        codes = dict(zip(("target", "preservation"), review["case_types"][case]))
        if codes["target"] != "F" or any(codes[flow] not in {"F", "V"}
                                          for flow in readiness[case]["collect_workflows"]):
            raise ValueError(f"{case}: main training must select actual form-save or validation workflows")
    for case in split["training_supplementary"]:
        if readiness[case]["training_type"] != "F" or readiness[case]["source"] != "supplementary":
            raise ValueError(f"{case}: supplementary training must identify its own form workflow")
    return cases, review, split, readiness


def build_report(cases, review, split, readiness):
    groups = {case: group for group in ("eval_holdout", "training_pilot", "training_supplementary",
                                      "training_pool", "auxiliary_pool", "deferred") for case in split[group]}
    classified = []
    for case in cases:
        codes = dict(zip(("target", "preservation"), review["case_types"][case["case_id"]]))
        classified.append({"case_id": case["case_id"], "slug": case["slug"], "repo": case["repo"],
            "base_commit": case["base_commit"], "reference_commit": case["reference_commit"],
            "cohort": groups.get(case["case_id"], "unselected"),
            "note": review.get("notes", {}).get(case["case_id"], ""),
            "workflows": [{"workflow": flow["category"], "workflow_id": flow["id"],
                "type": codes[flow["category"]], "label": review["categories"][codes[flow["category"]]],
                "purpose": flow["purpose"].strip()} for flow in yaml.safe_load(case["workflows"])["workflows"]]})
    main = [{"case_id": case_id, **readiness[case_id]} for case_id in sorted(readiness,
            key=lambda case_id: readiness[case_id]["priority"])]
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in (
        ROOT / "vendor/SWEeper-Bench/test.jsonl", ROOT / "configs/sft-case-types.yaml",
        ROOT / "configs/sft-split.yaml", ROOT / "configs/sft-app-readiness.yaml")}
    counts = {workflow: dict(collections.Counter(flow["type"] for case in classified
              for flow in case["workflows"] if flow["workflow"] == workflow))
              for workflow in ("target", "preservation")}
    return {"reviewed_on": review["reviewed_on"], "basis": review["basis"], "input_sha256": hashes,
            "official_cases": len(cases), "official_workflows": len(cases) * 2,
            "target_counts": counts["target"], "preservation_counts": counts["preservation"],
            "main_training": main, "planned_training_workflows": sum(len(case["collect_workflows"]) for case in main),
            "eval_holdout": split["eval_holdout"], "cases": classified}


if __name__ == "__main__":
    main()
