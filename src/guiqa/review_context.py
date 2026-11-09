"""Select public screenshot candidates for the atomic review contract."""

import json

from .review_evidence import is_edit, is_post_edit_action


def select_evidence(history, differences, before, after, nominated_steps=None):
    rows = {row["step"]: row for row in history if "action" in row and row["step"] <= after}
    operations = [rows[step] for step in sorted(rows) if step <= before]
    edits = [row for row in operations if is_edit(row["action"])]
    selected = []
    if edits:
        changed = [row for row in edits if differences.get(row["step"], 0) > 0]
        typed = (changed or edits)[-1]
        prior = [row["step"] for row in operations if row["step"] < edits[0]["step"]]
        if prior:
            nominated = set(nominated_steps or ())
            baselines = [step for step in prior if step in nominated]
            selected.append(((baselines or prior[-1:])[0], "before_input_candidate"))
        selected.append((typed["step"], "input_candidate" if changed else "input_attempt"))
        post_actions = [row for row in operations if row["step"] > typed["step"]
                        and is_post_edit_action(row["action"])]
        reload_step = next((row["step"] for row in operations if row["step"] > typed["step"]
                            and row["action"] and row["action"]["action"] == "press"
                            and row["action"].get("key") in ("Control+r", "F5")), None)
        if reload_step is not None:
            post = next((row for row in reversed(post_actions) if row["step"] < reload_step), None)
        else:
            post = next(iter(post_actions), None)
        if post:
            selected.append((post["step"], "submission_response_candidate"))
    else:
        triggers = [row for row in operations if is_post_edit_action(row["action"])]
        nominated = set(nominated_steps or ()) - {before, after}
        preferred = [row for row in triggers if row["step"] in nominated]
        changed = [row for row in triggers if differences.get(row["step"], 0) > 0]
        # With no typed input or intermediate nomination, the first change is only a candidate.
        trigger = next(iter(preferred or changed or triggers), None)
        if trigger:
            prior = [row["step"] for row in operations if row["step"] < trigger["step"]]
            if prior:
                selected.append((prior[-1], "before_trigger_candidate"))
            selected.append((trigger["step"], "trigger_response_candidate"))

    action = rows[after]["action"]
    reloaded = action and action["action"] == "press" and action.get("key") in ("Control+r", "F5")
    selected.append((after, "after_reload" if reloaded and after > before else "record_view_candidate"))
    selected.append((before, "outcome_candidate"))
    frames = {}
    for step, role in selected:
        if step in frames:
            frames[step]["roles"].append(role)
        elif len(frames) < 5:
            frames[step] = {"step": step, "roles": [role]}
    return [frames[step] for step in sorted(frames)]


def review_content(task, history, frames, paths):
    steps = [frame["step"] for frame in frames]
    actions = [row for row in history if "action" in row and row["step"] <= max(steps)][-31:]
    content = [{"type": "text", "text": task
                + "\nSupplied screenshot steps: " + json.dumps(steps)
                + "\nExecuted actions (up to 30 actions plus the initial view):\n"
                + json.dumps(actions, ensure_ascii=False)
                + "\nEvery screenshot shows the page AFTER the action at its step. The initial "
                  "step has no action. A before-trigger candidate is the preceding screenshot, "
                  "not the screenshot after that trigger."
                + "\nCandidate roles and visual changes do not prove submission or exact values. "
                  "The first changing click may be navigation. Establish accepted submission "
                  "from the actual action and visible response; distinguish Save, Cancel and "
                  "validation rejection. An old final value does not undo an accepted save."
                + "\nFor each field, locate its visible label and the input belonging to that "
                  "label before transcribing. Do not read a neighboring field. A visibly empty "
                  "field is the empty string \"\"; a field not visible or unreadable is null. "
                  "Cite only supplied screenshot steps. Action text is attempted input, not "
                  "a screenshot transcription."}]
    by_step = {row["step"]: row["action"] for row in actions}
    for frame in frames:
        step = frame["step"]
        text = (f"Screenshot step {step}, AFTER this executed action: "
                f"{json.dumps(by_step.get(step), ensure_ascii=False)}; "
                f"candidate roles: {', '.join(frame['roles'])}")
        content.extend([{"type": "text", "text": text}, {"type": "image", "image": paths[step]}])
    return content
