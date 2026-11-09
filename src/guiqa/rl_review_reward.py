"""Score structured review facts against independently annotated screenshot evidence."""

import json

from .review_contract import parse_review, review_guard


def score_review(raw, gold, steps, profile="persistence", approved_citations=None, *, rules=None,
                 truncated=False):
    score = _score_review(raw, gold, steps, profile, approved_citations, rules)
    if truncated:
        score.update(reward=None, strict_correct=False, certain_negative=False, proof_paths=[],
                     transport_error="length_limit_without_eos")
    return score


def _score_review(raw, gold, steps, profile, approved_citations, rules):
    """Gold is validated once at dataset ingress; alternate citations need visual review."""
    try:
        answer = parse_review(raw, steps, profile)
        rules = rules or {}
        _normalize_fields(answer, gold, rules)
    except ValueError as error:
        return {
            "reward": -1.0, "provisional_reward": -1.0, "prediction": None,
            "parse_error": str(error), "guard": None, "verdict_correct": False,
            "core_facts_correct": False, "strict_correct": False,
            "partial_fraction": 0.0, "matched_checks": 0, "total_checks": 0,
            "errors": ["format:invalid"], "checks": [], "needs_review": False,
            "unverified_citations": [], "unverified_fields": {"missing": [], "extra": []},
            "invalid_citations": [], "certain_negative": True, "proof_paths": ["format:invalid"],
        }

    approved = {**rules.get("approved_citations", {}), **(approved_citations or {})}
    invalid = rules.get("invalid_citations", {})
    checks, citations, rejected = [], [], []
    for key in ("task_kind", "expected_rejection"):
        _check(checks, key, answer[key], gold[key])
    _submission_checks(answer, gold, checks, citations, rejected, approved, invalid)
    unverified_fields = _field_checks(answer, gold, checks, citations, rejected, approved, invalid, rules)
    result_verified = True
    for key in ("result_steps", "evidence_steps"):
        verified = _citations(citations, rejected, key, answer[key], gold[key], approved, invalid)
        if key == "result_steps":
            result_verified = verified and bool(answer[key])
    _check(checks, "result_observed", answer["result_observed"], gold["result_observed"], result_verified)
    _check(checks, "result_steps.established", bool(answer["result_steps"]), bool(gold["result_steps"]),
           result_verified)

    guard = review_guard(answer, profile)
    errors = [check["path"] + ":mismatch" for check in checks if not check["correct"]]
    errors.extend("citation.invalid:" + item["path"] for item in rejected)
    core_correct = not errors and not any(unverified_fields.values())
    proofs = [check["path"] + ":mismatch" for check in checks
              if not check["correct"] and check["verified"]]
    proofs.extend("citation.invalid:" + item["path"] for item in rejected)
    if guard:
        errors.append("guard:" + guard)
        if guard in {"review_rejection_verdict_contradicts_task", "review_result_verdict_contradicts_fields"}:
            proofs.append("guard:" + guard)
    verdict_correct = answer["verdict"] == gold["verdict"]
    if not verdict_correct:
        errors.append("verdict:mismatch")
        proofs.append("verdict:mismatch")
    needs_review = bool(citations or any(unverified_fields.values()))
    conclusion_and_facts = verdict_correct and core_correct and guard is None
    strict_correct = conclusion_and_facts and not needs_review
    required_checks = [check for check in checks if check["partial_credit"]]
    matched = sum(check["correct"] for check in required_checks)
    fraction = matched / len(required_checks)
    provisional = round((1.0 if conclusion_and_facts else -1.0) + 0.2 * fraction, 8)
    reward = (-1.0 if proofs else None) if needs_review else provisional
    return {
        "reward": reward, "provisional_reward": provisional,
        "prediction": answer["verdict"], "parse_error": None, "guard": guard,
        "verdict_correct": verdict_correct, "core_facts_correct": core_correct,
        "strict_correct": strict_correct, "partial_fraction": fraction,
        "matched_checks": matched, "total_checks": len(required_checks), "errors": errors,
        "checks": checks, "needs_review": needs_review,
        "unverified_citations": citations, "unverified_fields": unverified_fields,
        "invalid_citations": rejected, "certain_negative": bool(needs_review and proofs), "proof_paths": proofs,
    }


def validate_gold(gold, steps, profile="persistence"):
    """Check the source annotation's contract and internal verdict consistency."""
    parsed = parse_review(json.dumps(gold, ensure_ascii=False), steps, profile)
    violation = review_guard(parsed, profile)
    if violation:
        raise ValueError("gold annotation contradicts its review contract: " + violation)
    return parsed


def _normalize_fields(answer, gold, rules):
    aliases = {name.strip().casefold(): canonical for name, canonical in rules.get("field_aliases", {}).items()}
    expected = {row["field"].strip().casefold(): row
                for row in [*gold["field_readings"], *rules.get("optional_fields", [])]}
    seen = set()
    for row in answer["field_readings"]:
        row["field"] = aliases.get(row["field"].strip().casefold(), row["field"])
        key = row["field"].strip().casefold()
        if key in seen:
            raise ValueError("duplicate field after applying verified aliases: " + row["field"])
        seen.add(key)
        if key in expected:
            annotated = expected[key]
            alternatives = rules.get("approved_requested_values", {}).get(annotated["field"], [])
            if row["requested_value"] in alternatives:
                row["requested_value"] = annotated["requested_value"]
            elif row["intent"] == annotated["intent"] == "preserve" and annotated["before"] is not None:
                targets = {row["requested_value"], annotated["requested_value"]}
                if targets == {"unchanged", annotated["before"]["value"]}:
                    row["requested_value"] = annotated["requested_value"]
            for stage in ("before", "after"):
                reading, reference = row[stage], annotated[stage]
                path = "field_readings." + annotated["field"] + "." + stage + ".value"
                if (reading is not None and reference is not None
                        and reading["value"] in rules.get("approved_values", {}).get(path, [])):
                    reading["value"] = reference["value"]


def _check(checks, path, actual, expected, verified=True, partial_credit=True):
    checks.append({"path": path, "actual": actual, "expected": expected, "correct": actual == expected,
                   "verified": verified, "partial_credit": partial_credit})


def _submission_checks(answer, gold, checks, citations, rejected, approved, invalid):
    actual, expected = answer["submission"]["evidence"], gold["submission"]["evidence"]
    _check(checks, "submission.evidence.present", actual is not None, expected is not None)
    verified = actual is None and expected is None
    if actual is not None and expected is not None:
        verified = _citations(citations, rejected, "submission.evidence.step", [actual["step"]],
                              [expected["step"]], approved, invalid)
    _check(checks, "submission.status", answer["submission"]["status"], gold["submission"]["status"], verified)


def _field_checks(answer, gold, checks, citations, rejected, approved, invalid, rules):
    actual = {row["field"].strip().casefold(): row for row in answer["field_readings"]}
    expected = {row["field"].strip().casefold(): row for row in gold["field_readings"]}
    optional = {row["field"].strip().casefold(): row for row in rules.get("optional_fields", [])}
    unverified = {
        "missing": [expected[key]["field"] for key in sorted(expected.keys() - actual.keys())],
        "extra": [actual[key]["field"] for key in sorted(actual.keys() - expected.keys() - optional.keys())],
    }
    # A different label may be a synonym or a different field; neither is inferred here.
    for key in sorted((expected.keys() | optional.keys()) & actual.keys()):
        observed, annotated = actual[key], expected.get(key, optional.get(key))
        required = key in expected
        path = "field_readings." + annotated["field"]
        stages_verified = {}
        for name in ("before", "after"):
            value, reference = observed[name], annotated[name]
            verified = value is None
            if value is not None:
                verified = _citations(citations, rejected, path + "." + name + ".step",
                                      [value["step"]], [] if reference is None else [reference["step"]],
                                      approved, invalid)
            stages_verified[name] = verified
            _check(checks, path + "." + name + ".value",
                   None if value is None else value["value"],
                   None if reference is None else reference["value"], verified, required)
        for name, verified in (("intent", True), ("requested_value", False),
                               ("input_matches_request", stages_verified["before"]),
                               ("result_matches_request", stages_verified["after"])):
            _check(checks, path + "." + name, observed[name], annotated[name], verified, required)
    return unverified


def _citations(citations, rejected, path, actual, expected, approved, invalid):
    accepted = set(expected) | set(approved.get(path, []))
    bad = sorted(set(actual) & set(invalid.get(path, [])))
    unverified = sorted(set(actual) - accepted - set(bad))
    if bad:
        rejected.append({"path": path, "claimed_steps": bad, "gold_steps": sorted(set(expected))})
    if unverified:
        citations.append({"path": path, "claimed_steps": unverified, "gold_steps": sorted(set(expected))})
    return not bad and not unverified


def resolve_training_reward(score):
    if score.get("transport_error") == "length_limit_without_eos":
        return {"reward": None, "certain_negative": False, "proof_paths": []}
    if score["reward"] is not None:
        return {"reward": score["reward"], "certain_negative": score.get("certain_negative", False),
                "proof_paths": score.get("proof_paths", [])}
    certain_errors = {"verdict:mismatch", "expected_rejection:mismatch",
                      "guard:review_rejection_verdict_contradicts_task",
                      "guard:review_result_verdict_contradicts_fields"}
    proofs = [error for error in score["errors"] if error in certain_errors]
    return {"reward": -1.0 if proofs else None, "certain_negative": bool(proofs), "proof_paths": proofs}
