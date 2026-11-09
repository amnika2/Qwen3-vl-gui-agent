"""Build and verify the explicitly selected ten-app SFT collection."""
import argparse
import collections
import hashlib
import json
import shutil
from pathlib import Path

import yaml

from guiqa.artifacts import write_json
from guiqa.sft_export import export
from verify_sft_dataset import verify_dataset


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("configs/sft-release.yaml"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / args.config).read_text())
    output = (root / (args.output or config["output"])).resolve()
    manifest = export(root, config["experiments"], output, bundle_images=config["bundle_images"],
                      deduplicate_trajectories=config["deduplicate_trajectories"])
    split = yaml.safe_load((root / "configs/sft-split.yaml").read_text())
    assert set(manifest["cases"]) == set(split["training_pilot"] + split["training_supplementary"])
    assert manifest["apps"] == 10
    verification = verify_dataset(root, output)
    write_json(output / "verification.json", verification)
    shutil.copy2(root / args.config, output / "release.yaml")
    shutil.copy2(root / "docs/sft-font-manifest.json", output / "font_manifest.json")
    by_app = collections.defaultdict(lambda: {"trajectories": 0, "samples": 0, "train_samples": 0,
        "validation_samples": 0, "review_labels": collections.Counter(), "review_profiles": collections.Counter()})
    for name in ("train", "validation"):
        for line in (output / f"{name}.jsonl").read_text().splitlines():
            sample = json.loads(line)
            stats = by_app[sample["case_id"]]
            stats["samples"] += 1
            stats[name + "_samples"] += 1
            if sample["phase"] == "review":
                stats["trajectories"] += 1
                stats["review_labels"][json.loads(sample["messages"][-1]["content"])["verdict"]] += 1
    for run in manifest["accepted_runs"]:
        by_app[run["case_id"]]["review_profiles"][run.get("review_profile", "persistence")] += 1
    summary = {"stage": "data_generation", "training_started": False, "dataset": str(output.relative_to(root)),
        "apps": manifest["apps"], "trajectories": manifest["trajectories"], "samples": manifest["samples"],
        "train_samples": manifest["train_samples"], "validation_samples": manifest["validation_samples"],
        "operation_samples": manifest["operation_samples"], "review_samples": manifest["review_samples"],
        "review_labels": manifest["review_labels"], "images": manifest["images"],
        "trajectory_deduplication": manifest["trajectory_deduplication"],
        "excluded_runs": manifest["excluded_runs"], "by_app": dict(by_app),
        "verification": verification,
        "release_config_sha256": hashlib.sha256((root / args.config).read_bytes()).hexdigest()}
    write_json(output / "collection_summary.json", summary)
    write_json(root / "docs/sft-generation-complete.json", summary)
    print(json.dumps({key: summary[key] for key in ("dataset", "apps", "trajectories", "samples",
        "train_samples", "validation_samples", "review_labels", "images", "trajectory_deduplication")},
        ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
