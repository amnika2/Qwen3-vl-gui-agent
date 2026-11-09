"""Derive stopping and bounded-review examples from recorded browser executions."""

import copy
import hashlib
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path

from .actions import parse_action
from .control import review_guard
from .model import OPERATION_PROMPTS, REVIEW_PROMPTS
from .sft_data import review_text, user_text


def build_dataset(root, source, output, extra_experiments=(), extra_reviews=(), visible_proofs=(), prefix_annotations=()):
    root, source, output = Path(root).resolve(), Path(source).resolve(), Path(output).resolve()
    parent = read_json(source / "manifest.json")
    proofs, annotations = {}, {}
    for path in visible_proofs:
        for run, proof in read_json(Path(path))["runs"].items():
            proofs[run] = {**proof, "annotation_source": str(path), "annotation_source_sha256": checksum(Path(path))}
    for path in prefix_annotations:
        for annotation in read_json(Path(path))["annotations"]:
            annotations.setdefault(annotation["source_run_id"], []).append(
                {**annotation, "annotation_source": str(path), "annotation_source_sha256": checksum(Path(path))})
    parent_hashes = {name: checksum(source / name) for name in
                     ("manifest.json", "train.jsonl", "validation.jsonl", "media_manifest.json")}
    if output == source or source in output.parents:
        raise ValueError("v2 output must be outside the preserved v1 directory")
    if parent_hashes["train.jsonl"] != parent["data_sha256"] or parent_hashes["validation.jsonl"] != parent["validation_sha256"]:
        raise ValueError("v1 content differs from its manifest")
    operations = defaultdict(list)
    for partition in ("train", "validation"):
        for sample in read_rows(source / f"{partition}.jsonl"):
            if sample["phase"] == "operation":
                sample = copy.deepcopy(sample)
                absolute_images(sample, source)
                sample["data_split"] = partition
                operations[sample["id"].rsplit(":", 1)[0]].append(sample)
    sources = [(accepted, operations[accepted["run_id"]]) for accepted in parent["accepted_runs"]]
    for experiment in extra_experiments:
        for cell in read_json(root / "runs" / experiment / "completed.json"):
            if not cell["matrix_matches"]:
                continue
            directory = root / cell["path"]
            result = read_json(directory / "result.json")
            raw = read_rows(directory / "sft/samples.jsonl")
            accepted = {key: result[key] for key in ("run_id", "case_id", "workflow", "build",
                        "scenario_id", "scenario_family", "scenario_fingerprint", "data_split")}
            accepted.update(review_profile=result.get("review_profile", "persistence"),
                            source_sha256=checksum(directory / "sft/samples.jsonl"))
            rows = [export_raw(row, f"{directory.name}:{index}", result)
                    for index, row in enumerate(raw) if row["phase"] == "operation"]
            sources.append((accepted, rows))
    output.mkdir(parents=True, exist_ok=False)
    samples, trajectories = [], []
    scenario_partitions = {}
    for accepted, rows in sources:
        run = root / "runs" / accepted["run_id"]
        trajectory, derived = derive_trajectory(run, accepted, rows, proofs.get(run.name, {}))
        scenario_partitions[trajectory["scenario_fingerprint"]] = trajectory["data_split"]
        trajectories.append(trajectory)
        samples.extend(derived)
    for path in extra_reviews:
        for index, line in enumerate(Path(path).read_text().splitlines()):
            row = json.loads(line)
            sample = export_prefix_review(root, row, scenario_partitions, annotations.get(row["source_run_id"], []))
            sample["derived_provenance"].update(source_review_file=str(Path(path).resolve()),
                                                source_review_file_sha256=checksum(Path(path)),
                                                source_review_line=index,
                                                source_review_line_sha256=hashlib.sha256(line.encode()).hexdigest())
            samples.append(sample)
    media = bundle_images(samples, output)
    for partition in ("train", "validation"):
        write_rows(output / f"{partition}.jsonl", [row for row in samples if row["data_split"] == partition])
    write_json(output / "media_manifest.json", media)
    kinds = Counter(row["sample_kind"] for row in samples)
    labels = Counter(json.loads(row["messages"][-1]["content"])["verdict"]
                     for row in samples if row["phase"] == "review")
    manifest = {"version": 2, "stage": "data_generation", "training_started": False,
                "parent_dataset": str(source.relative_to(root)), "parent_artifact_sha256": parent_hashes,
                "extra_experiments": list(extra_experiments), "extra_review_files": [str(path) for path in extra_reviews],
                "extra_review_sha256": {str(path): checksum(Path(path)) for path in extra_reviews},
                "visible_proof_sources": {str(path): checksum(Path(path)) for path in visible_proofs},
                "prefix_annotation_sources": {str(path): checksum(Path(path)) for path in prefix_annotations},
                "cases": sorted({row["case_id"] for row in samples}), "apps": len({row["case_id"] for row in samples}),
                "eval_holdout": parent["eval_holdout"],
                "trajectories": len(trajectories), "source_trajectories": trajectories,
                "trajectory_count": len(trajectories), "samples": len(samples),
                "sample_kinds": dict(kinds), "review_labels": dict(labels), "images": len(media),
                "operation_samples": sum(row["phase"] == "operation" for row in samples),
                "review_samples": sum(row["phase"] == "review" for row in samples),
                "train_samples": sum(row["data_split"] == "train" for row in samples),
                "validation_samples": sum(row["data_split"] == "validation" for row in samples),
                "data_sha256": checksum(output / "train.jsonl"),
                "validation_sha256": checksum(output / "validation.jsonl"),
                "media_manifest_sha256": checksum(output / "media_manifest.json"),
                "split_unit": "complete_source_scenario", "maximum_review_images": 4}
    write_json(output / "manifest.json", manifest)
    verification = verify_dataset(root, output)
    write_json(output / "verification.json", verification)
    for name, expected in parent_hashes.items():
        assert checksum(source / name) == expected
    return manifest, verification


def derive_trajectory(directory, accepted, operations, visible_proof=None):
    result = read_json(directory / "result.json")
    oracle = read_json(directory / "private/oracle.json")
    trace = read_rows(directory / "trace.jsonl")
    raw = read_rows(directory / "sft/samples.jsonl")
    if checksum(directory / "sft/samples.jsonl") != accepted["source_sha256"]:
        raise ValueError(f"{directory.name}: source samples changed")
    original = raw[-1]["assistant"]
    if original["verdict"] != result["oracle_verdict"] or original["verdict"] not in ("pass", "fail"):
        raise ValueError(f"{directory.name}: source is not a verified completed expert trajectory")
    profile = result.get("review_profile", "persistence")
    fingerprint = result.get("scenario_fingerprint", accepted["trajectory_fingerprint"] if "trajectory_fingerprint" in accepted else directory.name)
    metadata = {"case_id": result["case_id"], "workflow": result["workflow"],
                "data_split": result.get("data_split", "train"),
                "scenario_id": result.get("scenario_id", directory.name),
                "scenario_fingerprint": fingerprint, "review_profile": profile}
    provenance = {"source_run_id": directory.name, "source_samples_sha256": accepted["source_sha256"],
                  "source_trace_sha256": checksum(directory / "trace.jsonl"),
                  "source_oracle_sha256": checksum(directory / "private/oracle.json"),
                  "source_terminal_step": result["actions"], "source_verdict": original["verdict"]}
    rows = []
    for operation in operations:
        row = {**operation, **metadata, "sample_kind": "operation_action",
               "derived_provenance": {**provenance, "source_sample_id": operation["id"],
                                      "history_end_step": int(operation["id"].rsplit(":", 1)[1])}}
        rows.append(row)
    terminal = result["actions"]
    history = trace_history(trace)
    config = read_json(directory / "manifest.json")["config"]
    finish = {key: copy.deepcopy(original[key]) for key in ("action", "verdict", "observation")}
    finish["evidence_steps"] = [terminal]
    rows.append(make_sample(directory, metadata, "operation_finish", terminal, [terminal],
                            user_text(result["task"], history, config["history_actions"]), finish,
                            {**provenance, "history_end_step": terminal,
                             "label_basis": "verified_expert_completed_terminal_state"}))
    selection = review_selection(trace, oracle["events"], profile)
    review, basis = bounded_review(original, trace, oracle["events"], selection, directory, visible_proof or {})
    after = selection["after_step"]
    rows.append(make_sample(directory, metadata, "terminal_review", after, selection["screenshot_steps"],
                            review_text(result["task"], history[:after + 1]), review,
                            {**provenance, **selection, **basis, "history_end_step": after}))
    trajectory = {**metadata, **provenance, "samples": len(rows),
                  "terminal_review_verdict": review["verdict"]}
    return trajectory, rows


def review_selection(trace, events, profile):
    terminal = trace[-1]["step"]
    if profile == "business_outcome_v1":
        before = after = terminal
        kind = "business_terminal_no_controller_reload"
    elif is_reload(trace[-1]["action"]):
        before, after, kind = terminal - 1, terminal, "actual_reload"
    else:
        submitted = max(event["step"] for event in events if event["kind"] == "submit_response")
        reopened = [event["step"] for event in events if event["kind"].startswith("reopened")
                    and event.get("screenshot") and event["step"] > submitted]
        after = max(reopened)
        before, kind = after - 1, "actual_record_reopen"
    last_type = post_type = 0
    for row in trace[1:before + 1]:
        if row["action"]["action"] == "type":
            last_type, post_type = row["step"], 0
        elif last_type and not post_type:
            post_type = row["step"]
    steps = list(dict.fromkeys(step for step in (last_type, post_type, before, after) if step > 0))
    return {"review_alignment": kind, "before_step": before, "after_step": after,
            "last_type_step": last_type, "post_type_step": post_type, "screenshot_steps": steps}


def bounded_review(original, trace, events, selection, directory, visible_proof=None):
    visible_proof = visible_proof or {}
    selected = selection["screenshot_steps"]
    submitted = max(event["step"] for event in events if event["kind"] == "submit_response")
    input_proofs = final_input_steps(trace, events, submitted)
    final_events = [event for event in events if event["step"] > submitted and event.get("screenshot")
                    and event["kind"].startswith("reopened")]
    fields = {event["field"]: event["step"] for event in final_events if event["kind"] == "reopened_field"}
    result_proofs = sorted(set(fields.values())) if fields else [max(event["step"] for event in final_events)] if final_events else []
    errors = [event["step"] for event in events if event.get("screenshot") and
              event["kind"] in ("business_error", "business_error_ui", "duplicate_slug_error")]

    def covered(step, category):
        if step in visible_proof.get(f"unestablished_{category}_proof_steps", []):
            return False
        same_frame = step >= 0 and any(checksum(directory / f"screenshots/{step:03d}.png") ==
                                       checksum(directory / f"screenshots/{candidate:03d}.png") for candidate in selected)
        manual = any(frame["step"] in selected and step in frame.get(f"{category}_proof_steps", [])
                     for frame in visible_proof.get("frames", []))
        return same_frame or manual

    missing_inputs = [step for step in input_proofs if not covered(step, "input")]
    missing_results = [step for step in result_proofs if not covered(step, "result")]
    review = copy.deepcopy(original)
    review["requested_value_entered"] = original["requested_value_entered"] and not missing_inputs
    if visible_proof.get("unestablished_required_fields"):
        review["requested_value_entered"] = False
    review["final_record_visible"] = original["final_record_visible"] and not missing_results
    if "business_rejection_visible" in review:
        review["business_rejection_visible"] = original["business_rejection_visible"] and any(covered(step, "result") for step in errors)
    if review_guard(review, "business_outcome_v1" if "submission_observed" in review else "persistence"):
        review["verdict"] = "blocked"
    if review["verdict"] == "blocked":
        reasons = []
        if missing_inputs:
            reasons.append("the selected typed-state screenshots do not establish every requested input")
        if visible_proof.get("unestablished_required_fields"):
            reasons.append("the selected screenshots do not establish the requested " + ", ".join(visible_proof["unestablished_required_fields"]))
        if original["final_record_visible"] and missing_results:
            reasons.append("the selected result screenshots do not establish every final field of the same record")
        if original.get("business_rejection_visible") and not review["business_rejection_visible"]:
            reasons.append("the relevant business rejection is not visible in the selected screenshots")
        review["observation"] = "; ".join(reasons) + ". The evidence is incomplete, so the business outcome is blocked."
    review["evidence_steps"] = selected
    return review, {"label_basis": "selected_existing_screenshot_evidence",
                    "input_proof_steps": input_proofs, "missing_input_proof_steps": missing_inputs,
                    "result_proof_steps": result_proofs, "missing_result_proof_steps": missing_results,
                    "visible_proof_annotation": visible_proof}


def export_prefix_review(root, row, scenario_partitions, annotations=()):
    partition = scenario_partitions[row["source_scenario_fingerprint"]]
    if row["data_split"] != partition or row["assistant"]["verdict"] != "blocked":
        raise ValueError("prefix reviews must be blocked and inherit their source scenario partition")
    if len(row["screenshot_steps"]) > 4:
        raise ValueError("prefix review exceeds the frozen four-image window")
    directory = root / "runs" / row["source_run_id"]
    result = read_json(directory / "result.json")
    sample = export_raw(row, row.get("id", f"{directory.name}:prefix_review:{row['step']}"), result)
    answer = copy.deepcopy(row["assistant"])
    for annotation in annotations:
        answer[annotation["field"]] = annotation["expected"]
    sample["messages"][-1]["content"] = json.dumps(answer, ensure_ascii=False)
    sample.update(data_split=partition, sample_kind="prefix_review",
                  scenario_id=row["source_scenario_id"], scenario_fingerprint=row["source_scenario_fingerprint"],
                  review_profile=row.get("review_profile", "persistence"),
                  derived_provenance={"source_run_id": row["source_run_id"], "source_review_sha256": value_hash(row),
                                      "source_scenario_id": row["source_scenario_id"],
                                      "source_scenario_fingerprint": row["source_scenario_fingerprint"],
                                      "parent_run_id": row["parent_run_id"],
                                      "history_end_step": row["step"], "screenshot_steps": row["screenshot_steps"],
                                      "visible_record_annotations": list(annotations),
                                      "original_assistant": row["assistant"],
                                      "label_basis": "actual_unsubmitted_prefix_followed_by_recorded_controller_review"})
    return sample


def make_sample(directory, metadata, kind, step, screenshots, text, answer, provenance):
    phase = "review" if kind == "terminal_review" else "operation"
    prompts = REVIEW_PROMPTS if phase == "review" else OPERATION_PROMPTS
    content = [{"type": "text", "text": text}]
    for screenshot in screenshots:
        if phase == "review":
            content.append({"type": "text", "text": f"Screenshot step {screenshot}"})
        content.append({"type": "image", "image": str(directory / f"screenshots/{screenshot:03d}.png")})
    return {"id": f"{directory.name}:{kind}:{step}", **metadata, "phase": phase, "sample_kind": kind,
            "messages": [{"role": "system", "content": [{"type": "text", "text": prompts[metadata['review_profile']]}]},
                         {"role": "user", "content": content},
                         {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)}],
            "derived_provenance": provenance}


def verify_dataset(root, output):
    manifest = read_json(output / "manifest.json")
    partitions = {name: read_rows(output / f"{name}.jsonl") for name in ("train", "validation")}
    assert checksum(output / "train.jsonl") == manifest["data_sha256"]
    assert checksum(output / "validation.jsonl") == manifest["validation_sha256"]
    seen, fingerprints, executions = set(), defaultdict(set), {}
    parents = {row["source_run_id"]: row for row in manifest["source_trajectories"]}
    for partition, samples in partitions.items():
        for sample in samples:
            assert sample["id"] not in seen
            seen.add(sample["id"])
            assert sample["data_split"] == partition
            fingerprints[partition].add(sample["scenario_fingerprint"])
            assert sample["case_id"] not in manifest["eval_holdout"]
            provenance = sample["derived_provenance"]
            directory = root / "runs" / provenance["source_run_id"]
            if directory.name not in executions:
                executions[directory.name] = {"trace": read_rows(directory / "trace.jsonl"),
                                              "result": read_json(directory / "result.json"),
                                              "config": read_json(directory / "manifest.json")["config"],
                                              "raw": read_rows(directory / "sft/samples.jsonl")}
            execution = executions[directory.name]
            trace, result = execution["trace"], execution["result"]
            history = trace_history(trace)[:provenance["history_end_step"] + 1]
            profile = sample["review_profile"]
            review = sample["phase"] == "review"
            expected_prompt = (REVIEW_PROMPTS if review else OPERATION_PROMPTS)[profile]
            assert sample["messages"][0]["content"][0]["text"] == expected_prompt
            answer = json.loads(sample["messages"][-1]["content"])
            parse_action(json.dumps(answer), set(range(len(history))))
            if review:
                assert len(provenance["screenshot_steps"]) <= 4
                assert sample["messages"][1]["content"][0]["text"] == review_text(result["task"], history)
                assert review_guard(answer, profile) is None
                if sample["sample_kind"] == "terminal_review":
                    oracle = read_json(directory / "private/oracle.json")
                    selection = review_selection(trace, oracle["events"], profile)
                    expected, _ = bounded_review(execution["raw"][-1]["assistant"], trace, oracle["events"],
                                                 selection, directory, provenance["visible_proof_annotation"])
                    assert answer == expected
                    assert selection["screenshot_steps"] == provenance["screenshot_steps"]
                    assert selection["before_step"] == selection["after_step"] - (profile == "persistence")
                else:
                    parent = parents[provenance["parent_run_id"]]
                    assert parent["scenario_fingerprint"] == sample["scenario_fingerprint"]
                    assert parent["data_split"] == partition
                    source_file = Path(provenance["source_review_file"])
                    assert checksum(source_file) == provenance["source_review_file_sha256"]
                    original_line = source_file.read_text().splitlines()[provenance["source_review_line"]]
                    assert hashlib.sha256(original_line.encode()).hexdigest() == provenance["source_review_line_sha256"]
                    expected = copy.deepcopy(provenance["original_assistant"])
                    for annotation in provenance["visible_record_annotations"]:
                        assert annotation["field"] == "final_record_visible"
                        assert annotation["last_screenshot_step"] == provenance["history_end_step"]
                        assert expected[annotation["field"]] == annotation["original"]
                        expected[annotation["field"]] = annotation["expected"]
                    assert answer == expected
                    assert answer["verdict"] == "blocked" and not answer["submission_succeeded"]
            else:
                config = execution["config"]
                assert sample["messages"][1]["content"][0]["text"] == user_text(result["task"], history, config["history_actions"])
                if sample["sample_kind"] == "operation_finish":
                    assert provenance["history_end_step"] == result["actions"]
                    assert answer["action"] == "finish" and answer["verdict"] == result["oracle_verdict"]
                else:
                    index = int(provenance["source_sample_id"].rsplit(":", 1)[1])
                    assert answer == execution["raw"][index]["assistant"]
            image_steps = provenance.get("screenshot_steps", [provenance["history_end_step"]])
            images = [item["image"] for item in sample["messages"][1]["content"] if item["type"] == "image"]
            assert len(images) == len(image_steps)
            for image, step in zip(images, image_steps):
                assert step <= provenance["history_end_step"]
                assert checksum(output / image) == checksum(directory / f"screenshots/{step:03d}.png")
    assert not fingerprints["train"] & fingerprints["validation"]
    assert len(seen) == manifest["samples"]
    parent = root / manifest["parent_dataset"]
    for name, expected in manifest["parent_artifact_sha256"].items():
        assert checksum(parent / name) == expected
    for path, expected in {**manifest["visible_proof_sources"], **manifest["prefix_annotation_sources"]}.items():
        assert checksum(Path(path)) == expected
    for path in manifest["visible_proof_sources"]:
        for run_id, proof in read_json(Path(path))["runs"].items():
            for frame in proof.get("frames", []):
                if "screenshot_sha256" in frame:
                    assert checksum(root / "runs" / run_id / f"screenshots/{frame['step']:03d}.png") == frame["screenshot_sha256"]
    return {"passed": True, "samples": len(seen), "partitions": {k: len(v) for k, v in partitions.items()},
            "whole_scenario_partition_overlap": 0, "maximum_review_images": 4,
            "existing_screenshots_only": True, "existing_actions_and_histories_only": True,
            "profile_prompts_match": True, "v1_parent_bytes_preserved": True}


def export_raw(row, identifier, result):
    return {"id": identifier, "case_id": row["case_id"], "workflow": row["workflow"], "phase": row["phase"],
            "data_split": result.get("data_split", "train"),
            "messages": copy.deepcopy(row["messages"]) + [{"role": "assistant", "content": json.dumps(row["assistant"], ensure_ascii=False)}]}


def bundle_images(samples, output):
    media = {}
    for sample in samples:
        for message in sample["messages"][:-1]:
            for item in message["content"]:
                if item["type"] != "image":
                    continue
                source = Path(item["image"])
                sha = checksum(source)
                relative = f"images/{sha}.png"
                destination = output / relative
                if relative not in media:
                    destination.parent.mkdir(exist_ok=True)
                    shutil.copyfile(source, destination)
                    media[relative] = {"sha256": sha, "sources": []}
                media[relative]["sources"].append(str(source))
                item["image"] = relative
    return media


def absolute_images(sample, directory):
    for message in sample["messages"][:-1]:
        for item in message["content"]:
            if item["type"] == "image":
                item["image"] = str(directory / item["image"])


def trace_history(trace):
    return [{"step": row["step"], "action": row["action"], "result": row["execution_error"] or "executed"} for row in trace]


def is_reload(action):
    return action and action["action"] == "press" and action.get("key") == "Control+r"


def is_edit(action):
    return action and (action["action"] == "type" or
                       action["action"] == "press" and action.get("key") in ("Backspace", "Delete"))


def final_input_steps(trace, events, submitted):
    edits = [row["step"] for row in trace if row["step"] < submitted and is_edit(row["action"])]
    last_edit = max(edits)
    previous = max((event["step"] for event in events if event["kind"] in ("submit_response", "first_submit")
                    and event["step"] < last_edit), default=-1)
    final_by_focus, focused = {}, 0
    for row in trace[1:submitted]:
        action = row["action"]
        if action["action"] == "click" or action["action"] == "press" and action.get("key") == "Tab":
            focused = row["step"]
        if row["step"] > previous and is_edit(action):
            final_by_focus[focused] = row["step"]
    return sorted(final_by_focus.values())


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def value_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def read_json(path):
    return json.loads(path.read_text())


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
