"""Training-only evidence rules and G4 rewards for the expanded review pool."""

import copy
import hashlib
import json
from pathlib import Path

from .review_facts import parse_facts
from .rl_review_reward import score_review


SOURCE_FIELD_ALIASES = {
    "sweeper-105": {
        "source": "src/guiqa/expanded_training_collection.py:visible_fields; new_zone country/city inputs and saved table columns",
        "aliases": {"Country": "Saved country rows", "City": "Saved city rows",
                    "Saved country rows input": "Saved country rows",
                    "Saved city rows input": "Saved city rows"},
    },
    "sweeper-185": {
        "source": "src/guiqa/rl_targeted_collection.py:VisibleContext.capture; Header input and Header",
        "aliases": {"Header input": "Header"},
    },
    "sweeper-125": {
        "source": "src/guiqa/rl_targeted_collection.py:VisibleContext.capture; Weight input and Weight",
        "aliases": {"Weight input": "Weight"},
    },
}


def expanded_rules(row, dataset_root, catalog):
    """Extend rules after prepare_sample has validated the training row and bindings."""
    run_id = row["derived_provenance"]["source_run_id"]
    original = catalog["samples"].get(run_id, {})
    rules = copy.deepcopy(original)
    rules["field_aliases"] = {**catalog["case_aliases"].get(row["case_id"], {}),
                              **original.get("field_aliases", {})}
    if not row.get("run_directory"):
        return rules
    visible_path = Path(row["run_directory"]) / "private/visible.json"
    if not visible_path.is_file():
        return rules
    content = visible_path.read_bytes()
    visible = json.loads(content)
    snapshots = ({int(step): snapshot for step, snapshot in visible.items()}
                 if isinstance(visible, dict) else {snapshot["step"]: snapshot for snapshot in visible})
    steps = {frame["step"] for frame in row["review_frames"]}
    gold = json.loads(row["messages"][-1]["content"])
    evidence = gold["submission"]["evidence"]
    submit_step = evidence["step"] if evidence is not None else None
    approved = rules.setdefault("approved_citations", {})
    aliases = SOURCE_FIELD_ALIASES.get(row["case_id"], {"aliases": {}})
    canonical = {field["field"] for field in gold["field_readings"]}
    available = {name for step in steps for name in snapshots.get(step, {}).get("fields", {})}
    mapped_aliases = {}
    for alias, name in aliases["aliases"].items():
        if name in canonical and (name in available or name + " input" in available):
            rules["field_aliases"][alias] = name
            mapped_aliases[alias] = name
    additions = {}
    for field in gold["field_readings"]:
        for phase in ("before", "after"):
            reference = field[phase]
            if reference is None:
                continue
            path = f"field_readings.{field['field']}.{phase}.step"
            reference_snapshot = snapshots.get(reference["step"], {})
            for step in sorted(steps):
                snapshot = snapshots.get(step, {})
                if snapshot.get("url") != reference_snapshot.get("url"):
                    continue
                if phase == "after":
                    stage = step >= reference["step"]
                elif field["intent"] == "preserve":
                    stage = step <= reference["step"]
                else:
                    stage = (submit_step is not None and reference["step"] <= step < submit_step)
                names = ([field["field"] + " input", field["field"]]
                         if phase == "before" else [field["field"]])
                readings = [snapshot.get("fields", {}).get(name) for name in names]
                matches = any(reading is not None and reading.get("in_view", True)
                              and reading["value"] == reference["value"] for reading in readings)
                if stage and matches and step != reference["step"]:
                    additions.setdefault(path, []).append(step)
                    additions.setdefault("evidence_steps", []).append(step)
                    if phase == "after" and gold["result_observed"]:
                        additions.setdefault("result_steps", []).append(step)
    for path, values in additions.items():
        approved[path] = sorted(set(approved.get(path, [])) | set(values))
    rules["expanded_evidence"] = {
        "visible_file": str(visible_path), "visible_sha256": hashlib.sha256(content).hexdigest(),
        "selected_steps": sorted(steps), "added_citations": additions,
        "added_field_aliases": mapped_aliases,
        "field_alias_source": aliases.get("source") if mapped_aliases else None,
        "policy": "Same run, selected screenshot, same field and literal value, same page, correct phase; submission citations unchanged.",
    }
    return rules


def group_task_rewards(values):
    known = [value for value in values if value is not None]
    mean = sum(known) / len(known) if known else 0.0
    return [mean if value is None else value for value in values]


class ExpandedFactsReward:
    __name__ = "expanded_verified_review_facts"

    def __init__(self, samples, output, end_token_ids):
        self.samples = {sample["id"]: sample for sample in samples}
        self.output = Path(output)
        self.end_token_ids = end_token_ids
        self.groups = []

    def score_answers(self, sample, raw, completion_ids):
        scores, resolutions = [], []
        for answer, ids in zip(raw, completion_ids):
            truncated = ids[-1] not in self.end_token_ids
            try:
                rendered = json.dumps(parse_facts(answer, sample["steps"], sample["profile"]), ensure_ascii=False)
            except (ValueError, KeyError, TypeError):
                rendered = "{}"
            score = score_review(rendered, sample["gold"], sample["steps"], sample["profile"],
                                 rules=sample["reward_rules"], truncated=truncated)
            value = None if score["reward"] is None else 1.0 if score["strict_correct"] else -1.0
            scores.append(score)
            resolutions.append({"reward": value, "unknown": value is None,
                                "certain_negative": value == -1.0, "proof_paths": score["proof_paths"]})
        return scores, resolutions

    def __call__(self, completions, sample_id, completion_ids, **kwargs):
        returned = []
        for offset in range(0, len(completions), 4):
            sample = self.samples[sample_id[offset]]
            raw = [completion[0]["content"] for completion in completions[offset:offset + 4]]
            tokens = completion_ids[offset:offset + 4]
            scores, resolutions = self.score_answers(sample, raw, tokens)
            values = [item["reward"] for item in resolutions]
            rewards = group_task_rewards(values)
            known = [value for value in values if value is not None]
            group = {"index": len(self.groups), "sample_id": sample["id"],
                     "case_id": sample["case_id"], "label": sample["gold"]["verdict"],
                     "source_rewards": values, "rewards_returned_to_trainer": rewards,
                     "known_candidates": len(known), "unknown_candidates": 4 - len(known),
                     "all_unknown": not known,
                     "trusted_reward_difference": bool(known) and min(known) != max(known),
                     "same_score": bool(known) and min(known) == max(known),
                     "unknown_policy": "Impute the within-question known reward mean; unknown task advantage is zero."}
            self.groups.append(group)
            with (self.output / "reward-groups.jsonl").open("a") as handle:
                handle.write(json.dumps(group, ensure_ascii=False) + "\n")
            with (self.output / "rollouts.jsonl").open("a") as handle:
                for index, (answer, score, resolution, value) in enumerate(zip(raw, scores, resolutions, rewards)):
                    record = {"group": group["index"], "sample_id": sample["id"],
                              "candidate": index, "raw": answer, "score": score,
                              "source_reward": resolution["reward"],
                              "unknown": resolution["unknown"], "reward_returned_to_trainer": value}
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            returned.extend(rewards)
        return returned
