"""Review public business requirements against cited screenshot facts."""

import json


FORMAT = "business_evidence_v1"
TOP_FIELDS = {"action", "verdict", "observation", "evidence_steps", "checks"}
CHECK_FIELDS = {"id", "object", "expected", "observed", "evidence_steps", "status"}


def review_prompt(profile=FORMAT, requirements=None):
    prompt = (
        "Review the public browser task using the supplied screenshots and executed actions. "
        "Return one business_evidence_v1 finish JSON object. For each required business check, "
        "identify the same object, read the actual visible facts, cite their screenshot steps, "
        "and compare them with the public requirement. Action text describes an attempt, "
        "not a visible fact or a successful operation.\n"
        "Use checks with id, object, expected, observed, evidence_steps and status. Copy the "
        "id, object and expected from the supplied requirements. observed is a literal reading "
        "or a concise account of visible facts; use null when the facts are unestablished. "
        "status is pass, fail or unknown. Cite only supplied screenshot steps. A known status "
        "requires an observation and evidence.\n"
        "Check only the operations required by the task. A read-only task needs no save. "
        "For a requested mutation, establish the correct input or target, its actual trigger "
        "and the resulting state; compare the same object across stages. A save click or "
        "success notification alone does not establish the final state. A missing object "
        "in a filtered or unrelated view does not establish deletion. For a preservation "
        "requirement, read the original baseline and compare it with the final state. "
        "Correct rejection is pass when the public task requires rejection. Establish all "
        "required stages of a multi-step task and its final constraints.\n"
        "Use unknown when the target, trigger or necessary evidence is missing. Do not call "
        "an unsuccessful attempt an application defect. A confirmed violation makes the "
        "overall verdict fail; all required checks must pass for an overall pass; otherwise "
        "use blocked. observation summarizes visible results. Include all cited steps in "
        "the top-level evidence_steps.\n"
    )
    if requirements is not None:
        prompt += "Required public checks:\n" + json.dumps(requirements, ensure_ascii=False) + "\n"
    example = {"action": "finish", "verdict": "blocked", "observation": "Required evidence is absent.",
               "evidence_steps": [0], "checks": [{"id": "result", "object": "task object",
               "expected": "public requirement", "observed": None, "evidence_steps": [], "status": "unknown"}]}
    return prompt + "Output structure:\n" + json.dumps(example)


def json_schema(selected_steps, requirements=None):
    step = {"type": "integer", "enum": sorted(set(selected_steps))}
    steps = {"type": "array", "items": step}
    identity = {"type": "string", "minLength": 1}
    if requirements is not None:
        identity = {"type": "string", "enum": [item["id"] for item in requirements]}
    check = _object_schema({"id": identity, "object": {"type": "string", "minLength": 1},
        "expected": {"type": "string"},
        "observed": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "evidence_steps": steps, "status": {"enum": ["pass", "fail", "unknown"]}})
    checks = {"type": "array", "minItems": len(requirements) if requirements else 1, "items": check}
    if requirements is not None:
        checks["maxItems"] = len(requirements)
    return _object_schema({"action": {"enum": ["finish"]}, "verdict": {"enum": ["pass", "fail", "blocked"]},
        "observation": {"type": "string", "minLength": 1},
        "evidence_steps": {**steps, "minItems": 1}, "checks": checks})


def parse_review(raw, selected_steps, profile=FORMAT, requirements=None):
    text = raw.strip()
    if text.startswith("<tool_call>") and text.endswith("</tool_call>"):
        text = text[len("<tool_call>"):-len("</tool_call>")].strip()
    if text.startswith("```json\n") and text.endswith("```"):
        text = text[8:-3].strip()
    answer = json.loads(text)
    _validate(answer, selected_steps, requirements)
    return answer


def review_guard(answer, profile=FORMAT, requirements=None):
    statuses = [item["status"] for item in answer["checks"]]
    expected = "fail" if "fail" in statuses else "pass" if all(value == "pass" for value in statuses) else "blocked"
    return None if answer["verdict"] == expected else "review_verdict_contradicts_checks"


def select_evidence(history, differences, before, after, nominated_steps=None):
    rows = {row["step"]: row for row in history if "action" in row and row["step"] <= after}
    selected = {0, before, after, *(nominated_steps or [])} & rows.keys()
    actions = [row for row in rows.values() if row["action"]]
    for kind in ("type", "select", "upload"):
        candidates = [row["step"] for row in actions if row["action"]["action"] == kind]
        if candidates:
            selected.add(candidates[-1])
    for step in list(selected):
        if step > 0 and step - 1 in rows:
            selected.add(step - 1)
    return [{"step": step, "roles": ["public_business_evidence"]} for step in sorted(selected)]


def review_content(task, history, frames, paths, requirements=None):
    steps = [frame["step"] for frame in frames]
    actions = [row for row in history if "action" in row and row["step"] <= max(steps)]
    text = task + "\nRequired public checks:\n" + json.dumps(requirements or [], ensure_ascii=False)
    text += "\nExecuted actions:\n" + json.dumps(actions, ensure_ascii=False)
    text += "\nEach screenshot shows the page AFTER the action at its step. Step 0 is the initial state."
    content = [{"type": "text", "text": text}]
    for step in steps:
        content.extend([{"type": "text", "text": f"Screenshot step {step}"},
                        {"type": "image", "image": paths[step]}])
    return content


def _object_schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _validate(answer, selected_steps, requirements):
    if not isinstance(answer, dict) or set(answer) != TOP_FIELDS:
        raise ValueError("business review requires action, verdict, observation, evidence_steps and checks")
    if answer["action"] != "finish" or answer["verdict"] not in {"pass", "fail", "blocked"}:
        raise ValueError("business review requires a finish verdict")
    if not isinstance(answer["observation"], str) or not answer["observation"].strip():
        raise ValueError("business review requires a visible observation")
    _steps(answer["evidence_steps"], selected_steps)
    if not answer["evidence_steps"]:
        raise ValueError("business review must cite screenshots")
    if not isinstance(answer["checks"], list) or not answer["checks"]:
        raise ValueError("business review requires business checks")
    seen = set()
    for check in answer["checks"]:
        if not isinstance(check, dict) or set(check) != CHECK_FIELDS:
            raise ValueError("business check fields differ from the contract")
        for key in ("id", "object", "expected"):
            if not isinstance(check[key], str) or key != "expected" and not check[key].strip():
                raise ValueError("business check identity and expectation must be strings")
        if check["id"] in seen:
            raise ValueError("business check ids must be unique")
        seen.add(check["id"])
        if check["status"] not in {"pass", "fail", "unknown"}:
            raise ValueError("business check status must be pass, fail or unknown")
        if check["observed"] is not None and not isinstance(check["observed"], str):
            raise ValueError("business observation must be a string or null")
        _steps(check["evidence_steps"], selected_steps)
        if not set(check["evidence_steps"]).issubset(answer["evidence_steps"]):
            raise ValueError("check evidence must be included in the review evidence")
        if check["status"] != "unknown" and (check["observed"] is None or not check["evidence_steps"]):
            raise ValueError("known business results require visible facts and screenshots")
    if requirements is not None:
        declared = {item["id"]: item for item in requirements}
        if seen != declared.keys():
            raise ValueError("review must cover every required public check")
        for check in answer["checks"]:
            if any(check[key] != declared[check["id"]][key] for key in ("object", "expected")):
                raise ValueError("review changed a public business requirement")


def _steps(value, selected_steps):
    if not isinstance(value, list) or any(type(step) is not int or step not in selected_steps for step in value):
        raise ValueError("business evidence must cite supplied screenshot steps")
