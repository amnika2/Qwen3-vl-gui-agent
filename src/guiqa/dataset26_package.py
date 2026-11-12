"""Verify real-run provenance and package the frozen dataset once all jobs finish."""

import argparse
import copy
import hashlib
import json
import shutil
import time
from collections import Counter
from pathlib import Path

from .business_review import parse_review, review_guard
from .dataset26_runtime import action_target, operation_rows, read_rows, review_row, write_json


ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "runs/dataset26-v1"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    plan = json.loads((RUN / "plan.json").read_text())
    while True:
        records, status = inspect(plan)
        write_json(RUN / "status.json", status)
        if status["complete"]:
            regenerate_samples(records)
            package(plan, records)
            print(json.dumps(status), flush=True)
            return
        if not args.watch:
            print(json.dumps(status), flush=True)
            return
        time.sleep(20)


def inspect(plan):
    results, failures, running = [], [], []
    per_app, per_split, labels = Counter(), Counter(), Counter()
    for job in plan["jobs"]:
        directory = RUN / "collections" / job["case_id"] / job["run_id"]
        path = directory / "result.json"
        if not path.exists():
            continue
        result = json.loads(path.read_text())
        if not result.get("complete"):
            if result.get("error"):
                failures.append({"run_id": job["run_id"], "error": result["error"]})
            else:
                running.append(job["run_id"])
            continue
        for key in ("case_id", "split", "category", "build", "instance_id", "source_commit"):
            if result[key] != job[key]:
                raise ValueError(f"{job['run_id']} differs from the frozen {key}")
        results.append((directory, result))
        per_app[job["case_id"]] += 1
        per_split[job["split"]] += 1
        labels[result["verdict"]] += 1
    status = {"dataset": "dataset26-v1", "complete": len(results) == len(plan["jobs"]),
              "trajectories_completed": len(results), "trajectories_planned": len(plan["jobs"]),
              "review_samples_completed": len(results), "operation_samples_completed": 5 * len(results),
              "applications_complete": [app["case_id"] for app in plan["applications"]
                                        if per_app[app["case_id"]] == app["trajectories"]],
              "by_application": dict(per_app), "by_split": dict(per_split),
              "review_labels": dict(labels), "failed_runs": failures, "running_runs": running,
              "e2e_conditions_reserved": len(plan["e2e_jobs"])}
    return results, status


def package(plan, records):
    output = ROOT / "datasets/dataset26-v1"
    output.mkdir(parents=True, exist_ok=True)
    files, media, assets, lineage = {}, {}, {}, []
    partitions = {name: [] for name in ("train", "validation", "test")}
    for directory, result in records:
        for name, digest in result["sha256"].items():
            if checksum(directory / name) != digest:
                raise ValueError("run artifact changed: " + str(directory / name))
        operations = read_rows(directory / "operations.jsonl")
        review = json.loads((directory / "review.json").read_text())
        trace = read_rows(directory / result["trace_file"])
        answer = parse_review(review["messages"][-1]["content"],
                              {row["step"] for row in trace}, requirements=review["requirements"])
        if review_guard(answer, requirements=review["requirements"]):
            raise ValueError("review verdict differs from its required checks")
        if len(operations) != 5 or review["phase"] != "review":
            raise ValueError("each trajectory requires five operations and one review")
        for row in operations:
            before = next(frame for frame in trace if frame["step"] == row["step"])
            after = next(frame for frame in trace if frame["step"] == row["target_step"])
            if json.loads(row["messages"][-1]["content"]) != action_target(after["action"]):
                raise ValueError("operation target differs from the recorded next action")
            image = row["messages"][1]["content"][-1]["image"]
            if checksum(Path(image)) != checksum(directory / before["screenshot"]):
                raise ValueError("operation screenshot is not its actual preceding frame")
        rows = copy.deepcopy([*operations, review])
        for row in rows:
            bundle_media(row, output, media)
            bundle_upload(row, output, assets)
        partitions[result["split"]].extend(rows)
        lineage.append({**{key: result[key] for key in (
            "case_id", "split", "category", "build", "instance_id", "task_family_id", "source_commit", "verdict")},
            "run_directory": str(directory), "result_sha256": checksum(directory / "result.json")})
    expected = {"train": 3600, "validation": 1080, "test": 960}
    for name, rows in partitions.items():
        if len(rows) != expected[name]:
            raise ValueError("packaged sample quota differs: " + name)
        for suffix, content in (("", rows), ("-reviews", [row for row in rows if row["phase"] == "review"]),
                                ("-operations", [row for row in rows if row["phase"] == "operation"])):
            path = output / (name + suffix + ".jsonl")
            path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in content))
            files[path.name] = {"sha256": checksum(path), "samples": len(content)}
    write_json(output / "media_manifest.json", media)
    write_json(output / "assets_manifest.json", assets)
    write_json(output / "lineage.json", lineage)
    shutil.copyfile(RUN / "plan.json", output / "plan.json")
    shutil.copytree(RUN / "e2e", output / "e2e", dirs_exist_ok=True)
    manifest = {"dataset": "dataset26-v1", "complete": True, "applications": 26,
                "trajectories": 940, "review_samples": 940, "operation_samples": 4700,
                "samples": 5640, "files": files, "images": len(media),
                "upload_files": len(assets),
                "plan_sha256": checksum(output / "plan.json"),
                "source_sha256": plan["source_sha256"], "review_format": "business_evidence_v1",
                "e2e_reserved": plan["e2e_jobs"], "e2e_executed": False,
                "e2e_conditions_sha256": checksum(output / "e2e/conditions.jsonl"),
                "training_started": False, "teacher_model_calls": 0,
                "human_pixel_review_claimed": False}
    write_json(output / "manifest.json", manifest)
    readme = """# dataset26-v1

| 划分 | 应用 | 真实轨迹 | 审查样本 | 操作样本 |
| --- | ---: | ---: | ---: | ---: |
| 训练集 | 12 | 600 | 600 | 3000 |
| 验证集 | 6 | 180 | 180 | 900 |
| 测试集 | 8 | 160 | 160 | 800 |
| 合计 | 26 | 940 | 940 | 4700 |

每条轨迹产生 1 个审查样本和 5 个操作样本，共 5640 个样本。
轨迹数包含两个运行版本：A 为缺陷源码，B 为修复源码；每个参数实例分别运行 A、B。
操作类别 A（身份验证）和 B（复杂业务流程）仅出现在测试集。

`task_family_id` 标识业务任务族，`instance_id` 标识参数实例，`condition` 或 `condition_id` 记录触发条件。
仅修改输入值或触发条件的实例归在同一任务族。26 个应用对应 26 个业务任务族，
470 对参数实例共运行 940 条轨迹。

`train.jsonl`、`validation.jsonl`、`test.jsonl` 包含操作与审查样本；
同名前缀的 `-operations.jsonl`、`-reviews.jsonl` 分别保存两种样本。
模型输入输出位于 `messages`，其余字段用于分组和溯源。
`requirements` 列出公开必查项，审查标签逐项记录对象、预期、实际读数、截图步骤和检查状态。
操作样本的 `native_controls` 同时出现在公开输入中，声明可调用的原生选择与上传控件。
图片和上传文件路径相对于本目录，分别见 `images/`、`files/`。
`plan.json` 固定应用划分与源码版本，`lineage.json` 指向原始轨迹和结果哈希，
`manifest.json`、`media_manifest.json`、`assets_manifest.json` 记录文件校验信息。
原始轨迹、截图和实例日志保存在 `runs/dataset26-v1/collections/`。
原始审查样本按字节保存在每条轨迹的 `private/recorded-review.json`，规范化样本由其再生。

`e2e/conditions.jsonl` 提供 16 个独立端到端测试条件（8 个测试应用各 A、B 版本），
以及所需上传文件。模型端到端测试尚未执行，标记为 `executed=false`。

从仓库根目录执行数据构建、训练格式与 processor 校验、端到端运行：

```bash
.venv/bin/python scripts/build_dataset26.py
.model-venv/bin/python scripts/verify_dataset26_training.py
.venv/bin/python scripts/run_dataset26_e2e.py --method script --output runs/dataset26-v1/e2e-script
.model-venv/bin/python scripts/run_dataset26_e2e.py --method qwen-controlled --output runs/dataset26-v1/e2e-model
```

`script` 用于真实应用的脚本执行与独立 Oracle 校准；`qwen-controlled` 执行模型端到端评测。
训练使用 `configs/sft-train-dataset26-v1.yaml`。历史 `scripts/export_sft.py` 面向旧数据布局，
本数据通过 `scripts/build_dataset26.py` 直接生成上述消息格式。

| 划分 | 类别 | 应用编号 | 应用 |
| --- | --- | --- | --- |
"""
    readme += "".join(f"| {app['split']} | {app['category']} | {app['case_id']} | {app['slug']} |\n"
                      for app in plan["applications"])
    (output / "README.md").write_text(readme)
    write_json(RUN / "completion.json", {"complete": True, "output": str(output),
               "manifest_sha256": checksum(output / "manifest.json"), "targets": plan["targets"]})


def bundle_media(row, output, media):
    for message in row["messages"][:-1]:
        for item in message["content"]:
            if item["type"] != "image":
                continue
            source = Path(item["image"])
            digest = checksum(source)
            name = f"images/{digest}.png"
            target = output / name
            if name not in media:
                target.parent.mkdir(exist_ok=True)
                shutil.copyfile(source, target)
                media[name] = {"sha256": digest, "bytes": target.stat().st_size}
            item["image"] = name


def checksum(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def regenerate_samples(records):
    for directory, result in records:
        original = read_rows(directory / "operations.jsonl")[0]
        metadata = {key: value for key, value in original.items()
                    if key not in {"id", "phase", "step", "target_step", "run_directory", "messages",
                                   "requirements", "native_controls"}}
        review_path = directory / "review.json"
        recorded_path = directory / "private/recorded-review.json"
        if not recorded_path.exists():
            recorded_path.write_bytes(review_path.read_bytes())
        recorded = json.loads(json.loads(recorded_path.read_text())["messages"][-1]["content"])
        if result["case_id"] == "sweeper-087":
            family = "sweeper-087:account_login"
            metadata["task_family_id"] = family
            result["task_family_id"] = family
        trace = read_rows(directory / result["trace_file"])
        review = review_row(directory, metadata, result["task"], trace, recorded)
        write_json(review_path, review)
        result["requirements"] = review["requirements"]
        result["verdict"] = json.loads(review["messages"][-1]["content"])["verdict"]
        rows = operation_rows(directory, trace, metadata, result["task"])
        target = directory / "operations.jsonl"
        target.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
        result["sha256"]["operations.jsonl"] = checksum(target)
        result["sha256"]["review.json"] = checksum(review_path)
        result["sha256"]["private/recorded-review.json"] = checksum(recorded_path)
        write_json(directory / "result.json", result)


def bundle_upload(row, output, assets):
    if row["phase"] != "operation":
        return
    action = json.loads(row["messages"][-1]["content"])
    if action["action"] != "upload":
        return
    source = Path(action["file"])
    digest = checksum(source)
    name = "files/" + digest + "-" + source.name
    if name not in assets:
        target = output / name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, target)
        assets[name] = {"sha256": digest, "filename": source.name, "bytes": target.stat().st_size}
    action["file"] = name
    row["messages"][-1]["content"] = json.dumps(action, ensure_ascii=False)
    row["messages"][1]["content"][0]["text"] += "\nAvailable upload file: " + name


if __name__ == "__main__":
    main()
