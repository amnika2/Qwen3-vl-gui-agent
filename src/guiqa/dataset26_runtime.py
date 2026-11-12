"""Record real browser trajectories for the eight-operation dataset."""

import hashlib
import json
from pathlib import Path

from .actions import browser_action as action_target
from .browser import VIEWPORT, execute, settle
from .business_review import parse_review, review_content, review_guard, review_prompt
from .dataset26_controls import public_controls
from .dataset26_review_labels import normalize_review, public_requirements


OPERATION_PROMPT = """Carry out the given GUI testing task using the current screenshot and action history.
Return one JSON action. Coordinates range from 0 to 1000. Available actions are click,
type, press, scroll, wait, navigate, select and upload. Test the stated business requirement
and collect visible evidence for the final review. An application defect is a test result."""
REVIEW_PROMPT = """Review the supplied screenshots and executed actions against the public business requirement.
Return JSON with action=finish, verdict=pass|fail|blocked, observation, evidence_steps and checks.
PASS means the required behavior is demonstrated; correct rejection of an invalid operation is PASS.
FAIL means visible evidence demonstrates a violation. BLOCKED means the required evidence is absent.
Describe actual visible results, identify the same object across stages, and cite supplied screenshot steps.
Attempted action text does not prove a visible value or successful execution."""


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


class DatasetRecorder:
    def __init__(self, page, directory, metadata, task):
        self.page = page
        self.directory = Path(directory).resolve()
        self.metadata = metadata
        self.task = task
        self.index = 0
        self.history = []
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "screenshots").mkdir(exist_ok=True)
        self.capture("initial authenticated business state")

    def capture(self, note="", action=None):
        if self.history and action is None:
            self.index += 1
        settle(self.page)
        name = f"screenshots/{self.index:03d}.png"
        self.page.screenshot(path=str(self.directory / name))
        row = {"step": self.index, "action": action, "screenshot": name,
               "description": note, "url": self.page.url, "execution_error": None}
        self.history.append(row)
        with (self.directory / "trace.jsonl").open("a") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return self.index

    def step(self, action, wait_url=None, navigation=False):
        if navigation:
            with self.page.expect_navigation(wait_until="domcontentloaded"):
                execute(self.page, action)
        else:
            execute(self.page, action)
        if wait_url is not None:
            self.page.wait_for_url(wait_url, wait_until="domcontentloaded")
        self.index += 1
        return self.capture(action["action"], action)

    def goto(self, url):
        return self.step({"action": "navigate", "url": url})

    def click(self, locator, wait_url=None, navigation=False):
        if isinstance(locator, str):
            locator = self.page.locator(locator)
        box = locator.bounding_box()
        center = box["y"] + box["height"] / 2
        if center < 0 or center >= VIEWPORT["height"]:
            self.scroll(int(center - VIEWPORT["height"] / 2))
            box = locator.bounding_box()
        point = [round((box["x"] + box["width"] / 2) / (VIEWPORT["width"] - 1) * 1000),
                 round((box["y"] + box["height"] / 2) / (VIEWPORT["height"] - 1) * 1000)]
        return self.step({"action": "click", "coordinate": point}, wait_url=wait_url, navigation=navigation)

    def fill(self, locator, text):
        self.click(locator)
        self.press("Control+a")
        return self.step({"action": "type", "text": str(text)})

    def press(self, key):
        return self.step({"action": "press", "key": key})

    def scroll(self, pixels):
        return self.step({"action": "scroll", "pixels": int(pixels)})

    def wait(self, seconds):
        return self.step({"action": "wait", "seconds": seconds})

    def select(self, locator, label=None, value=None):
        action = {"action": "select", "selector": locator}
        if label is not None:
            action["label"] = label
        if value is not None:
            action["value"] = value
        return self.step(action)

    def upload(self, locator, path):
        path = Path(path).resolve()
        relative = "uploads/" + path.name
        target = self.directory / relative
        target.parent.mkdir(exist_ok=True)
        if path != target:
            target.write_bytes(path.read_bytes())
        return self.step({"action": "upload", "selector": locator,
                          "file": str(target)})

    def finish(self, verdict, observation, evidence_steps=None, checks=None):
        return import_trace(self.directory, self.metadata, self.task, verdict,
                            observation, evidence_steps, checks)


def import_trace(directory, metadata, task, verdict, observation, evidence_steps=None, checks=None):
    directory = Path(directory).resolve()
    trace = read_rows(directory / "trace.jsonl")
    operations = operation_rows(directory, trace, metadata, task)
    evidence_steps = list(dict.fromkeys(evidence_steps or [trace[0]["step"], trace[-1]["step"]]))
    by_step = {row["step"]: row for row in trace}
    if verdict not in {"pass", "fail", "blocked"} or any(step not in by_step for step in evidence_steps):
        raise ValueError("review verdict or screenshot evidence is invalid")
    answer = {"action": "finish", "verdict": verdict, "observation": observation,
              "evidence_steps": evidence_steps, "checks": checks or []}
    history = [{"step": row["step"], "action": row["action"]} for row in trace]
    content = [{"type": "text", "text": task + "\nExecuted actions:\n" + json.dumps(history, ensure_ascii=False)}]
    for step in evidence_steps:
        content.extend([{"type": "text", "text": f"Screenshot step {step}, after the recorded action"},
                        {"type": "image", "image": str(directory / by_step[step]["screenshot"])}])
    review = {**metadata, "id": metadata["instance_id"] + "-" + metadata["build"] + "-review",
              "phase": "review", "review_format": "business_evidence_v1", "run_directory": str(directory),
              "messages": [{"role": "system", "content": [{"type": "text", "text": REVIEW_PROMPT}]},
                           {"role": "user", "content": content},
                           {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)}]}
    original = directory / "private/recorded-review.json"
    if not original.exists():
        write_json(original, review)
    review = review_row(directory, metadata, task, trace, answer)
    write_json(directory / "review.json", review)
    (directory / "operations.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in operations))
    files = [directory / "trace.jsonl", directory / "operations.jsonl", directory / "review.json"]
    files.append(original)
    files.extend(directory / row["screenshot"] for row in trace)
    files.extend(path for path in (directory / "uploads").glob("*") if path.is_file())
    result = {**metadata, "complete": True, "task": task,
              "verdict": json.loads(review["messages"][-1]["content"])["verdict"],
              "requirements": review["requirements"],
              "trace_file": "trace.jsonl", "screenshot_files": [row["screenshot"] for row in trace],
              "operation_samples": 5, "review_samples": 1, "review_sample": "review.json",
              "action_count": len(trace) - 1,
              "sha256": {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}}
    write_json(directory / "result.json", result)
    return result


def review_row(directory, metadata, task, trace, recorded):
    answer, requirements = normalize_review(metadata["case_id"], task, recorded)
    steps = answer["evidence_steps"]
    parse_review(json.dumps(answer), set(steps), requirements=requirements)
    violation = review_guard(answer, requirements=requirements)
    if violation:
        raise ValueError(violation)
    by_step = {row["step"]: row for row in trace}
    history = [{"step": row["step"], "action": row["action"]} for row in trace]
    frames = [{"step": step} for step in steps]
    paths = {step: str(directory / by_step[step]["screenshot"]) for step in steps}
    return {**metadata, "requirements": requirements,
            "id": metadata["instance_id"] + "-" + metadata["build"] + "-review",
            "phase": "review", "review_format": "business_evidence_v1", "run_directory": str(directory),
            "messages": [{"role": "system", "content": [{"type": "text", "text": review_prompt(requirements=requirements)}]},
                         {"role": "user", "content": review_content(task, history, frames, paths, requirements)},
                         {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)}]}


def operation_rows(directory, trace, metadata, task):
    application_url = json.loads((directory / "private/instance/instance.json").read_text())["url"]
    requirements = public_requirements(metadata["case_id"], task)
    controls = public_controls(metadata["case_id"])
    candidates = []
    for index, row in enumerate(trace[1:], 1):
        action = row["action"]
        if action and action["action"] in {"click", "type", "press", "scroll", "navigate", "select", "upload"}:
            candidates.append(index)
    if len(candidates) < 5:
        raise ValueError(f"trajectory has only {len(candidates)} operation examples, needs five")
    chosen = []
    for kind in ("upload", "type", "select"):
        matching = [index for index in candidates if trace[index]["action"]["action"] == kind]
        if matching:
            chosen.append(matching[-1] if kind == "type" else matching[0])
    remaining = [index for index in candidates if index not in chosen]
    slots = 5 - len(chosen)
    chosen.extend(remaining[round(index * (len(remaining) - 1) / (slots - 1))] for index in range(slots))
    chosen.sort()
    rows = []
    for sequence, index in enumerate(chosen):
        before, after = trace[index - 1], trace[index]
        history = [{"step": row["step"], "action": row["action"]} for row in trace[max(0, index - 12):index]]
        text = task + "\nApplication URL: " + application_url
        text += "\nRequired public checks:\n" + json.dumps(requirements, ensure_ascii=False)
        text += "\nNative control tools:\n" + json.dumps(controls, ensure_ascii=False)
        text += "\nRecent actions:\n" + json.dumps(history, ensure_ascii=False)
        rows.append({**metadata, "requirements": requirements, "native_controls": controls,
                     "id": metadata["instance_id"] + "-" + metadata["build"] + f"-op-{sequence}",
                     "phase": "operation", "step": before["step"], "target_step": after["step"],
                     "run_directory": str(directory),
                     "messages": [{"role": "system", "content": [{"type": "text", "text": OPERATION_PROMPT}]},
                                  {"role": "user", "content": [{"type": "text", "text": text},
                                   {"type": "image", "image": str(directory / before["screenshot"])}]},
                                  {"role": "assistant", "content": json.dumps(action_target(after["action"]), ensure_ascii=False)}]})
    return rows
