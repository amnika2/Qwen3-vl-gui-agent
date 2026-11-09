import json

from .browser import BrowserTimeout, execute
from .model import OPERATION_PROMPTS, REVIEW_PROMPTS
from .oracle import judge


def user_text(task, history, history_actions):
    """Replicates Qwen.generate's user text exactly; keep in sync with model.py."""
    window = history[-history_actions:]
    return (task + "\nRecent actions (oldest first):\n" + json.dumps(window)
            + f"\nCurrent screenshot step: {history[-1]['step'] if history else 0}")


def review_text(task, history):
    """Replicates Qwen.assess's user text exactly; keep in sync with model.py."""
    return task + "\nExecuted actions:\n" + json.dumps(history)


class SftRecorder:
    def __init__(self, context, task, config):
        self.context = context
        self.task = task
        self.config = config
        self.path = context.directory / "sft/samples.jsonl"
        self.path.parent.mkdir(exist_ok=True)
        self.rows = 0

    def _write(self, row):
        with self.path.open("a") as file:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.rows += 1

    def operation(self, action):
        context = self.context
        profile = context.result.get("review_profile", "persistence")
        step = context.result["actions"]
        screenshot = context.directory / f"screenshots/{step:03d}.png"
        text = user_text(self.task, context.history, self.config["history_actions"])
        row = {"phase": "operation", "case_id": context.result["case_id"],
               "workflow": context.result["workflow"], "build": context.result.get("build"),
               "step": step, "screenshot": str(screenshot),
               "messages": [{"role": "system", "content": [{"type": "text", "text": OPERATION_PROMPTS[profile]}]},
                            {"role": "user", "content": [
                                {"type": "text", "text": text},
                                {"type": "image", "image": str(screenshot)}]}],
               "assistant": action}
        self._write(row)
        context.event({"step": step, "sft": "operation_sample"})

    def final_review(self, steps, review, frames=None):
        context = self.context
        profile = context.result.get("review_profile", "persistence")
        evidence_format = self.config.get("review_format") == "evidence_v1"
        if evidence_format:
            from .review_evidence import parse_review, review_content, review_prompt
            from .sft_review_evidence import replay_frames

            if frames is None:
                after = context.result["actions"]
                last_action = context.history[-1]["action"]
                reloaded = last_action and last_action["action"] == "press" and last_action.get("key") == "Control+r"
                frames = replay_frames(context.directory, context.history, after - int(bool(reloaded)), after)
            steps = [frame["step"] for frame in frames]
            review = parse_review(json.dumps(review), set(steps), profile)
        screenshots = [(step, context.directory / f"screenshots/{step:03d}.png")
                       for step in dict.fromkeys(steps)]
        prompt = REVIEW_PROMPTS[profile]
        content = [{"type": "text", "text": review_text(self.task, context.history)},
                   *[item for step, path in screenshots
                     for item in [{"type": "text", "text": f"Screenshot step {step}"},
                                  {"type": "image", "image": str(path)}]]]
        if evidence_format:
            prompt = review_prompt(profile)
            content = review_content(self.task, context.history, frames,
                                     {step: str(path) for step, path in screenshots})
        row = {"phase": "review", "case_id": context.result["case_id"],
               "workflow": context.result["workflow"], "build": context.result.get("build"),
               "step": context.result["actions"], "screenshot": str(screenshots[-1][1]),
               "screenshot_steps": [step for step, _ in screenshots],
               "messages": [{"role": "system", "content": [{"type": "text", "text": prompt}]},
                            {"role": "user", "content": content}],
               "label_source": "observed_run_v2", "assistant": review}
        if evidence_format:
            row.update(review_format="evidence_v1", review_frames=frames)
        if profile != "persistence":
            row["review_profile"] = profile
        self._write(row)
        context.event({"step": context.result["actions"], "sft": "review_sample",
                       "screenshot_steps": [step for step, _ in screenshots]})


def sft_collect(context, task, flow, config, reviewer=None):
    """Record operation-phase samples for a scripted action-space flow.

    Mirrors the agent loop's screenshot/history conventions: the sample for an
    action uses the screenshot and history captured before that action runs.
    """
    recorder = SftRecorder(context, task, config)
    before_reload = replay_flow(context, flow, recorder)
    review = reviewer(context) if reviewer else observed_review(context)
    recorder.final_review([*flow.review_steps, flow.input_step, flow.submit_step,
                           before_reload, context.result["actions"]], review)
    context.result.update(agent_verdict=review["verdict"], observation=review["observation"],
                          evidence_steps=review["evidence_steps"])
    context.result["termination_reason"] = "sft_recorded"
    context.result["sft_samples"] = recorder.rows
    return recorder


def replay_flow(context, flow, recorder=None):
    before_reload = 0
    for make_action in flow.steps:
        action = make_action(context.page)
        if recorder:
            recorder.operation(action)
        try:
            if context.result["actions"] + 1 in flow.navigation_steps:
                with context.page.expect_navigation(wait_until="domcontentloaded"):
                    outcome = execute(context.page, action)
            else:
                outcome = execute(context.page, action)
        except BrowserTimeout as timeout:
            context.capture("Reference action failed", action, error=str(timeout))
            raise
        context.result["actions"] += 1
        step = context.result["actions"]
        if action["action"] == "press" and action.get("key") == "Control+r":
            before_reload = step - 1
        context.capture(action["action"], {**action, **outcome})
    return before_reload


def observed_review(context):
    evidence = context.observer.evidence
    verdict, reason = judge(evidence)
    submission = next((event for event in reversed(evidence.events) if event["kind"] == "submit_response"), {})
    entered = evidence.submitted
    accepted = evidence.response_valid and submission.get("accepted", True)
    visible = evidence.reopened and evidence.same_object and evidence.actual is not None
    if verdict not in ("pass", "fail") or not (entered and accepted and visible):
        verdict = "blocked"
    return {"action": "finish", "verdict": verdict, "requested_value_entered": entered, "submission_succeeded": accepted,
            "final_record_visible": visible,
            "observation": f"Expected {evidence.expected!r}; observed {evidence.actual!r} after reopening. {reason}.",
            "evidence_steps": [context.result["actions"]]}
