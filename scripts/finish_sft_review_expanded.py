"""Run the trained review adapter's test and save its comparison with v4."""

import json
from pathlib import Path
import subprocess
import time


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/sft-review-expanded-v1"


def main():
    training = ROOT / "runs/sft-qwen3vl-review-expanded-v1"
    wait_for_exit(training / "train-exit.json")
    wait_for_exit(REPORT / "v4/exit.json")
    command = [str(ROOT / ".model-venv/bin/python"), "-u",
               str(ROOT / "scripts/evaluate_sft_review_expanded.py"),
               "--adapter", str(training / "adapter"),
               "--output", str(REPORT / "expanded"), "--gpus", "0,1"]
    subprocess.run(command, cwd=ROOT, check=True)
    baseline = json.loads((REPORT / "v4/summary.json").read_text())
    candidate = json.loads((REPORT / "expanded/summary.json").read_text())
    completion = json.loads((training / "completion.json").read_text())
    metrics = ("macro_f1", "terminal_verdict_accuracy", "strict_accuracy", "defect_recall",
               "normal_false_alarm_rate", "defect_false_pass_rate")
    result = {
        "completed": baseline["completed"] and candidate["completed"],
        "training": completion,
        "test_samples": candidate["planned_samples"],
        "v4": baseline["all"], "expanded": candidate["all"],
        "delta": {key: candidate["all"][key] - baseline["all"][key] for key in metrics},
        "case_id": {case: {"v4": baseline["case_id"][case], "expanded": values}
                    for case, values in candidate["case_id"].items()},
    }
    (REPORT / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"completed": result["completed"], "delta": result["delta"]}, indent=2), flush=True)


def wait_for_exit(path):
    while not path.exists():
        time.sleep(15)
    status = json.loads(path.read_text())["exit_code"]
    if status:
        raise SystemExit(status)


if __name__ == "__main__":
    main()
