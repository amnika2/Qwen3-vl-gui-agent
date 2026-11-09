"""Apply saved visual adjudications without changing raw diagnostic predictions."""

import argparse
import copy
import hashlib
import json
from pathlib import Path

from guiqa.rl_review_diagnostic import gold_answer, load_run, summarize, write_json, write_rows
from guiqa.rl_review_reward import score_review


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    manifest, selected = load_run(output)
    decisions_path = output / "adjudications.json"
    decisions = json.loads(decisions_path.read_text())
    sources = {row["id"]: row for row in selected}
    validate_adjudications(decisions, sources, Path(manifest["dataset"]))
    predictions = [json.loads(line) for line in (output / "predictions.jsonl").read_text().splitlines()]
    reviewed = [apply_adjudication(row, sources[row["id"]], decisions["samples"].get(row["id"]))
                for row in predictions]
    summary = summarize(reviewed, manifest)
    summary["adjudication_sha256"] = hashlib.sha256(decisions_path.read_bytes()).hexdigest()
    summary["raw_predictions_sha256"] = hashlib.sha256((output / "predictions.jsonl").read_bytes()).hexdigest()
    summary["visually_adjudicated_samples"] = len(decisions["samples"])
    write_rows(output / "adjudicated_predictions.jsonl", reviewed)
    write_json(output / "adjudicated_summary.json", summary)
    print(json.dumps({"completed": summary["completed"], "all": summary["all"]}, ensure_ascii=False, indent=2))


def apply_adjudication(row, source, decision):
    result = copy.deepcopy(row)
    if decision is None or row["parse_error"] is not None:
        return result
    gold = gold_answer(source)
    score = score_review(row["raw"], gold, {frame["step"] for frame in source["review_frames"]},
                         source["review_profile"], rules=decision)
    if row["truncated"]:
        score.update(reward=None, strict_correct=False)
    result.update(score, adjudicated=True, adjudicated_invalid_citations=score["invalid_citations"])
    return result


def validate_adjudications(decisions, sources, dataset):
    for identifier, decision in decisions["samples"].items():
        source = sources[identifier]
        images = [dataset / item["image"] for message in source["messages"][:-1]
                  for item in message["content"] if item["type"] == "image"]
        by_step = {str(frame["step"]): image for frame, image in zip(source["review_frames"], images)}
        for step, expected in decision["reviewed_image_hashes"].items():
            if hashlib.sha256(by_step[step].read_bytes()).hexdigest() != expected:
                raise ValueError("visually reviewed source image changed: " + identifier + ":" + step)


if __name__ == "__main__":
    main()
