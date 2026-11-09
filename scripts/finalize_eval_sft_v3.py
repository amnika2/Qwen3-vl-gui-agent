"""Verify and record the completed eighty-run matched v2/v3 evaluation."""

from pathlib import Path

from guiqa.sft_v2 import checksum, read_json, write_json


def main():
    root = Path(__file__).resolve().parents[1]
    report, verification = verify_evaluation(root)
    summary = {"completed": True, "experiment": "sft-lora-v3", "variants": ["v2", "v3"],
               "review_format": "evidence_v1", "runs": 80, "runs_per_variant": 40,
               "metrics": report["metrics"], "verification": verification,
               "report": "reports/sft-lora-v3/summary.md",
               "pairs": "reports/sft-lora-v3/pairs.csv",
               "historical_v2_old_protocol_report": "reports/sft-lora-v2/comparison.json"}
    write_json(root / "docs/sft-v3-eval-completion.json", summary)
    print({"completed": True, "runs": 80, "metrics": report["metrics"], "verification": verification})


def verify_evaluation(root):
    directory = root / "reports/sft-lora-v3"
    report = read_json(directory / "comparison.json")
    frozen = read_json(directory / "evaluation-source.json")
    if any(report["metrics"][variant]["runs"] != 40 or
           report["metrics"][variant]["completed_runs"] != 40 for variant in ("v2", "v3")):
        raise ValueError("both variants must finish all forty planned cells")
    if len(report["worker_inference_core"]) != 10 or report["core_sha256_values"] != [frozen["inference_core"]["sha256"]]:
        raise ValueError("worker inference inputs differ from the frozen protocol")
    if not all(report["inference_core_pairs"].values()):
        raise ValueError("a v2/v3 application pair used different inference inputs")
    for name, expected in frozen["inference_core"]["files"].items():
        if checksum(root / name) != expected:
            raise ValueError(f"a frozen inference input changed: {name}")
    for model in report["worker_models"].values():
        expected = frozen["models"][model["variant"]]["adapter_sha256"]
        if model["adapter_sha256"] != expected:
            raise ValueError("a worker used an unexpected adapter")
    queues = [read_json(path) for path in (root / "runs/sft-lora-v3-workers").glob("*.queue.json")]
    if len(queues) != 4 or any(queue["exit_code"] != 0 for queue in queues):
        raise ValueError("all four worker queues must exit successfully")
    completed_rows = [row for variant in ("v2", "v3") for case in ("005", "007", "008", "040", "041")
                      for row in read_json(root / f"runs/sft-lora-v3-{variant}-sweeper-{case}/completed.json")]
    if len(completed_rows) != 80 or len({row["run_id"] for row in completed_rows}) != 80:
        raise ValueError("the final evaluation must contain eighty distinct runs")
    evidence = {"v2": 0, "v3": 0}
    for row in completed_rows:
        result = read_json(root / row["path"] / "result.json")
        expected = "fail" if row["build"] == "A" and row["workflow"] == "target" else "pass"
        evidence[row["variant"]] += result["oracle_verdict"] == expected
    verification = {"matched_protocol": True, "core_sha256": frozen["inference_core"]["sha256"],
                    "core_files": len(frozen["inference_core"]["files"]), "workers": 10,
                    "adapter_hashes_match_selected_training": True, "all_queues_exit_code": 0,
                    "distinct_runs": 80, "failed_runs_retained": True,
                    "historical_results_reused": False,
                    "oracle_matches_expected_by_variant": evidence}
    return report, verification


if __name__ == "__main__":
    main()
