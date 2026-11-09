"""Compare the selected v2/v3 adapters under one evidence review protocol."""

import argparse
import csv
import hashlib
import json
from pathlib import Path

import yaml

from evaluate_sft import cell_key
from evaluate_sft_v2 import inference_core, matches, variant_metrics
from guiqa.artifacts import write_json
from guiqa.cli import load_config, load_model
from guiqa.runner import run_case

VARIANTS = ("v2", "v3")
PAIR_FIELDS = ("run_id", "agent_verdict", "oracle_verdict", "actions", "model_calls",
               "termination_reason", "control_trigger", "review_guard", "review_images",
               "review_model_verdict", "field_readings", "submission_step", "review_evidence")


def main():
    args = arguments()
    root = Path(__file__).resolve().parents[1]
    suite = yaml.safe_load((root / args.config).read_text())
    if args.case and args.case not in suite["cases"]:
        raise ValueError("--case must be one of the configured frozen evaluation cases")
    if args.command == "report":
        print(report(root, args.experiment, suite))
        return 0
    core = inference_core(root, suite)
    variants = (args.variant,) if args.variant else VARIANTS
    cases = (args.case,) if args.case else suite["cases"]
    models = {variant: model_record(root, suite, variant) for variant in variants}
    workers = [prepare(root, args, suite, variant, case, core, models[variant])
               for variant in variants for case in cases]
    if args.command == "plan":
        print(json.dumps([{"worker": directory.name, "planned": len(schedule["planned"]),
                           "pending": len(pending), "adapter_sha256": schedule["model"]["adapter_sha256"],
                           "inference_core_sha256": core["sha256"]}
                          for directory, schedule, pending in workers], indent=2))
        return 0
    directory, schedule, pending = workers[0]
    directory.mkdir(parents=True, exist_ok=True)
    if not (directory / "schedule.json").exists():
        write_json(directory / "schedule.json", schedule)
        write_json(directory / "completed.json", [])
    completed = json.loads((directory / "completed.json").read_text())
    if pending:
        model = load_model(suite["method"], schedule["config"])
        for cell in pending:
            path, result = run_case(root, **cell, config=schedule["config"], model=model,
                                    port=args.port, experiment=directory.name)
            row = {**cell, "variant": args.variant, "run_id": result["run_id"],
                   "path": str(path.relative_to(root)), "inference_core_sha256": core["sha256"],
                   "adapter_sha256": schedule["model"]["adapter_sha256"],
                   "termination_reason": result["termination_reason"]}
            completed.append(row)
            write_json(directory / "completed.json", completed)
            print(json.dumps(row), flush=True)
            if result["termination_reason"] == "interrupted":
                break
    print(json.dumps({"worker": str(directory), "report_command":
                      f"python scripts/evaluate_sft_v3.py report --experiment {args.experiment}"}))
    return 0


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "run", "report"))
    parser.add_argument("--config", type=Path, default=Path("configs/sft-eval-v3.yaml"))
    parser.add_argument("--experiment", default="sft-lora-v3")
    parser.add_argument("--variant", choices=VARIANTS)
    parser.add_argument("--case")
    parser.add_argument("--port", type=int, default=13260)
    args = parser.parse_args()
    if args.command == "run" and (not args.variant or not args.case):
        parser.error("run requires --variant and --case; one worker runs eight cells")
    return args


def model_record(root, suite, variant):
    adapter = (root / suite["models"][variant]).resolve()
    completion_path = adapter.parent / "completion.json"
    completion = json.loads(completion_path.read_text())
    actual = hashlib.sha256((adapter / "adapter_model.safetensors").read_bytes()).hexdigest()
    if not completion["completed"] or actual != completion["adapter_sha256"]:
        raise ValueError(f"{variant}: exported adapter differs from completed training")
    return {"variant": variant, "adapter_path": str(adapter), "adapter_sha256": actual,
            "training_completion": str(completion_path),
            "training_completion_sha256": hashlib.sha256(completion_path.read_bytes()).hexdigest(),
            "selected_epoch": completion["selected_epoch"]}


def prepare(root, args, suite, variant, case, core, selected_model):
    directory = root / "runs" / f"{args.experiment}-{variant}-{case}"
    if (directory / "schedule.json").exists():
        schedule = json.loads((directory / "schedule.json").read_text())
        if (schedule["inference_core"] != core or schedule["model"] != selected_model
                or schedule["suite"] != suite):
            raise ValueError("evaluation inputs changed; use a new experiment name")
        completed = json.loads((directory / "completed.json").read_text())
    else:
        config = load_config(root / suite["agent_config"])
        config.update(adapter_path=selected_model["adapter_path"],
                      adapter_sha256=selected_model["adapter_sha256"], inference_core_sha256=core["sha256"])
        planned = [{"case_id": case, "build": build, "workflow": workflow,
                    "method": suite["method"], "repeat": repeat}
                   for build in suite["builds"] for workflow in suite["workflows"]
                   for repeat in range(suite["repeats"])]
        schedule = {"experiment": directory.name, "variant": variant, "suite": suite,
                    "config": config, "planned": planned, "inference_core": core, "model": selected_model}
        completed = []
    done = {cell_key(cell) for cell in completed if cell["termination_reason"] != "interrupted"}
    return directory, schedule, [cell for cell in schedule["planned"] if cell_key(cell) not in done]


def report(root, experiment, suite):
    results, protocols, models = collect_results(root, experiment, suite)
    metrics = {variant: review_metrics(results, variant, suite) for variant in VARIANTS}
    pairs = paired_rows(results, protocols, experiment, suite)
    core_hashes = sorted({core["sha256"] for core in protocols.values()})
    protocol_pairs = {row["case_id"]: row["inference_core_matches"] for row in pairs}
    directory = root / "reports" / experiment
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "pairs.csv").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(pairs[0]))
        writer.writeheader()
        writer.writerows(pairs)
    write_json(directory / "comparison.json", {
        "experiment": experiment, "variants": list(VARIANTS), "planned_per_variant": len(pairs),
        "method": suite["method"], "review_format": "evidence_v1", "historical_results_reused": False,
        "metrics": metrics, "inference_core_pairs": protocol_pairs, "core_sha256_values": core_hashes,
        "all_worker_inference_core_identical": len(core_hashes) == 1,
        "worker_inference_core": protocols, "worker_models": models,
    })
    write_summary(directory, experiment, suite, metrics, len(pairs), protocol_pairs)
    return directory


def collect_results(root, experiment, suite):
    cells, protocols, models = {}, {}, {}
    for variant in VARIANTS:
        for case in suite["cases"]:
            directory = root / "runs" / f"{experiment}-{variant}-{case}"
            if not (directory / "schedule.json").exists():
                continue
            schedule = json.loads((directory / "schedule.json").read_text())
            protocols[directory.name] = schedule["inference_core"]
            models[directory.name] = schedule["model"]
            for cell in json.loads((directory / "completed.json").read_text()):
                cells[(variant, *cell_key(cell))] = cell
    results = {key: {**cell, **json.loads((root / cell["path"] / "result.json").read_text())}
               for key, cell in cells.items()}
    return results, protocols, models


def review_metrics(results, variant, suite):
    rows = [row for key, row in results.items() if key[0] == variant]
    normal = [row for row in rows if not (row["build"] == "A" and row["workflow"] == "target")]
    readings = [reading for row in rows for reading in row.get("field_readings", [])]
    return {**variant_metrics(results, variant, suite),
            "normal_correct": sum(matches(row, "pass") for row in normal),
            "review_invalid_output": sum(row["termination_reason"] == "review_invalid_output" for row in rows),
            "structured_review_runs": sum(bool(row.get("field_readings")) for row in rows),
            "field_readings": len(readings),
            "visible_input_readings": sum(reading.get("input_value") is not None for reading in readings),
            "visible_result_readings": sum(reading.get("observed_value") is not None for reading in readings)}


def paired_rows(results, protocols, experiment, suite):
    pairs = []
    for case in suite["cases"]:
        left = protocols.get(f"{experiment}-v2-{case}")
        right = protocols.get(f"{experiment}-v3-{case}")
        same = left == right if left and right else None
        for build in suite["builds"]:
            for workflow in suite["workflows"]:
                for repeat in range(suite["repeats"]):
                    row = {"case_id": case, "build": build, "workflow": workflow, "repeat": repeat,
                           "expected": "fail" if build == "A" and workflow == "target" else "pass",
                           "inference_core_matches": same}
                    for variant in VARIANTS:
                        result = results.get((variant, case, build, workflow, repeat), {})
                        for field in PAIR_FIELDS:
                            value = result.get(field, "" if result else "not_started")
                            row[f"{variant}_{field}"] = json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value
                    pairs.append(row)
    return pairs


def write_summary(directory, experiment, suite, metrics, planned, protocol_pairs):
    lines = [f"# {experiment}", "", f"v2和v3均加载已选LoRA，在相同evidence_v1协议下重新评估；每种权重计划{planned}格。", "",
             "| 权重 | 完成 | 正确格 | 正常正确 | 缺陷检出 | 正常误报 | 成对正确 | 四格全对 | 主动且正确 | blocked | 审查格式错误 | 环境故障 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    sets = len(suite["cases"]) * suite["repeats"]
    for variant, item in metrics.items():
        lines.append(f"| {variant} | {item['completed_runs']}/{planned} | {item['correct_cells']}/{planned} | "
                     f"{item['normal_correct']}/{item['normal_runs']} | {item['detected']}/{item['target_runs']} | "
                     f"{item['false_positives']}/{item['normal_runs']} | {item['correct_target_pairs']}/{sets} | "
                     f"{item['correct_four_cell_sets']}/{sets} | {item['active_finish_correct']}/{planned} | "
                     f"{item['agent_blocked']} | {item['review_invalid_output']} | {item['infra_error']} |")
    lines.extend(["", "正确格要求模型、独立判定和预期三者一致。blocked、inconclusive、格式错误和环境故障均保留在分母中。",
                  "失败不重试，interrupted的后续同格结果覆盖报告视图，原始行保留。重复不增加应用样本数。",
                  f"逐应用推理核心一致性：{json.dumps(protocol_pairs, ensure_ascii=False)}。",
                  "", "字段读值计数表示已输出的非null原文数量，不表示OCR或语义读取正确率。"])
    for variant, item in metrics.items():
        lines.extend(["", f"{variant}：主动finish {item['active_finish']}/{item['runs']}，"
                      f"独立判定inconclusive {item['inconclusive']}，中位动作 {item['median_actions']}；"
                      f"结构化审查 {item['structured_review_runs']}，输入/结果可见读值 "
                      f"{item['visible_input_readings']}/{item['visible_result_readings']}。",
                      f"guard原因：{json.dumps(item['guard_reasons'], ensure_ascii=False)}。"])
    lines.extend(["", "逐格配对及原文引用见 [pairs.csv](pairs.csv)，权重和推理核心哈希见 [comparison.json](comparison.json)。"])
    (directory / "summary.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
