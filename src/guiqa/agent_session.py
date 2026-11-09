"""Planning, rolling context and cross-task experience for a GUI episode."""

import json
import sys
import time

from jsonschema import ValidationError, validate

from .agent_tools import MCPConnections, ToolRegistry
from .artifacts import write_json


CONTEXT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "plan": {"type": "array", "minItems": 2, "maxItems": 6, "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"step": {"type": "string", "maxLength": 100},
                           "status": {"enum": ["pending", "in_progress", "done"]}},
            "required": ["step", "status"]}},
        "summary": {"type": "string", "maxLength": 600},
        "reflection": {"type": "string", "maxLength": 240},
        "next_goal": {"type": "string", "maxLength": 160}},
    "required": ["plan", "summary", "reflection", "next_goal"]}

EXPERIENCE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {key: {"type": "array", "maxItems": 6,
                         "items": {"type": "string", "maxLength": 220}}
                   for key in ("procedure", "lessons")},
    "required": ["procedure", "lessons"]}


def context_schema(plan):
    properties = dict(CONTEXT_SCHEMA["properties"])
    if plan:
        del properties["plan"]
        properties["statuses"] = {"type": "array", "minItems": len(plan), "maxItems": len(plan),
                                  "items": {"enum": ["pending", "in_progress", "done"]}}
    else:
        properties["summary"] = {"enum": [""]}
        properties["reflection"] = {"enum": [""]}
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": list(properties)}


class AgentSession:
    def __init__(self, context, task, model, config, deadline):
        self.context, self.task, self.model, self.config = context, task, model, config
        self.settings = config["agent_runtime"]
        self.deadline = deadline
        self.summarized_count = 0
        self.last_refresh_step = 0
        self.tool_observations = []
        self.state = {"task": task, "phase": "planning", "step": 0, "plan": [],
                      "summary": "", "reflection": "", "next_goal": "Plan the task",
                      "compacted_through_step": None, "retrieved_experiences": [],
                      "context_updates": 0, "external_tool_calls": 0,
                      "finish_deferrals": 0,
                      "planner_adapter": self.settings.get("planner_adapter", "actor")}
        memory = {"name": "memory", "command": sys.executable,
                  "args": ["-m", "guiqa.mcp_server", "--database", self.settings["memory_database"],
                           "--namespace", self.settings["memory_namespace"]]}
        self.connections = MCPConnections([memory, *self.settings.get("mcp_servers", [])])

    def __enter__(self):
        self.connections.__enter__()
        self.tools = ToolRegistry(self.context.page, self.context.directory, self.connections)
        self.context.tools = self.tools
        write_json(self.context.directory / "tool-catalog.json", {
            "tools": list(self.tools.catalog.values())})
        return self

    def start(self):
        result = self.tools.call("memory__search_experiences", {
            "task": self.task, "case_id": self.context.result["case_id"],
            "workflow": self.context.result["workflow"], "limit": self.settings["memory_top_k"]},
            caller="runtime_recall")
        if result["is_error"]:
            raise RuntimeError(result["text"])
        self.state["retrieved_experiences"] = result["data"]["experiences"]
        self.refresh("initial_plan")

    def progress(self):
        return {"task_state": self.state, "tools": list(self.tools.catalog.values()),
                "tool_observations": self.tool_observations[-3:]}

    def refresh(self, reason):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            return
        window = self.config["history_actions"]
        cutoff = max(self.summarized_count, len(self.context.history) - window)
        forgotten = self.context.history[self.summarized_count:cutoff]
        raw = self.model.update_context(
            self.task, self.screenshot(), {**self.state, "tool_observations": self.tool_observations[-3:]}, forgotten,
            self.context.history[cutoff:], reason, remaining)
        self.context.result["model_calls"] += 1
        self.context.event({"phase": "context", "reason": reason,
                            "step": self.context.result["actions"], "raw_output": raw,
                            "planner_adapter": self.state["planner_adapter"]})
        update = json.loads(raw)
        validate(update, context_schema(self.state["plan"]))
        if self.state["plan"]:
            update["plan"] = [{**step, "status": status}
                              for step, status in zip(self.state["plan"], update.pop("statuses"))]
        self.state.update(update)
        self.summarized_count = cutoff
        self.last_refresh_step = self.context.result["actions"]
        if cutoff:
            self.state["compacted_through_step"] = self.context.history[cutoff - 1]["step"]
        self.state["context_updates"] += 1
        self.state["phase"] = "executing"
        with (self.context.directory / "context-events.jsonl").open("a") as handle:
            handle.write(json.dumps({"reason": reason, "step": self.last_refresh_step,
                                     "compacted_actions": forgotten, "update": update,
                                     "raw": raw}, ensure_ascii=False) + "\n")
        self.persist()

    def after_action(self, execution_error=None):
        self.persist()
        if execution_error:
            self.refresh("execution_error: " + execution_error)
        elif self.context.result["actions"] - self.last_refresh_step >= self.settings["refresh_actions"]:
            self.refresh("periodic_context_update")

    def defer_finish(self, action):
        if (action["verdict"] == "blocked" or self.state["finish_deferrals"]
                or self.context.result["actions"] >= self.config["max_actions"] - 1):
            return False
        self.refresh("finish_requested: " + action["observation"])
        unfinished = [step["step"] for step in self.state["plan"] if step["status"] != "done"]
        if not unfinished or time.monotonic() >= self.deadline:
            return False
        feedback = ("Finish postponed: the current plan still requires " + "; ".join(unfinished)
                    + ". Continue with next_goal and verify the submitted result. "
                      "If an actual obstacle prevents completion, report blocked and explain it.")
        self.state["finish_deferrals"] += 1
        self.context.history.append({"step": self.context.result["actions"], "result": feedback})
        self.context.event({"phase": "finish_deferred", "step": self.context.result["actions"],
                            "unfinished_steps": unfinished, "proposed_finish": action})
        self.persist()
        return True

    def call_tool(self, action):
        result = self.tools.call(action["name"], action["arguments"])
        self.tool_observations.append({"name": action["name"], "result": result})
        self.state["external_tool_calls"] += 1
        reason = "tool_error: " + result["text"] if result["is_error"] else "tool_result: " + action["name"]
        self.refresh(reason)
        self.persist()

    def finish(self):
        completed = self.context.result["termination_reason"] in {
            "model_finished", "controlled_review", "controlled_review_guarded"}
        self.finalize_state("finished" if completed else "stopped")
        if not completed:
            return
        if time.monotonic() < self.deadline:
            self.state["final_observation"] = self.context.result.get("observation", "")
            try:
                self.refresh("final_review_completed: " + self.context.result["agent_verdict"])
            except (ValueError, ValidationError, RuntimeError) as error:
                self.state["context_error"] = f"{type(error).__name__}: {error}"
        if time.monotonic() < self.deadline:
            public_episode = {
                "task_kind": self.context.result.get("task_kind", "unknown"),
                "fields": [{key: field[key] for key in ("field", "intent")}
                           for field in self.context.result.get("field_readings", [])],
                "actions": [{"step": row["step"], "action": row["action"]["action"],
                             **({"key": row["action"]["key"]} if row["action"]["action"] == "press" else {})}
                            for row in self.context.history if row.get("action")],
                "execution_feedback": [row["result"] for row in self.context.history if row["result"] != "executed"],
                "submission_status": self.context.result.get("submission", {}).get("status", "unknown"),
                "result_observed": self.context.result.get("result_observed", False)}
            try:
                raw = self.model.learn_episode(public_episode, self.deadline - time.monotonic())
                self.context.result["model_calls"] += 1
                self.context.event({"phase": "learn", "raw_output": raw})
                experience = json.loads(raw)
                validate(experience, EXPERIENCE_SCHEMA)
                saved = self.tools.call("memory__save_experience", {
                    "case_id": self.context.result["case_id"], "workflow": self.context.result["workflow"],
                    "task": self.task, "source_run_id": self.context.result["run_id"], **experience},
                    caller="runtime_learn")
                if saved["is_error"]:
                    raise RuntimeError(saved["text"])
                write_json(self.context.directory / "experience.json", {**experience, **saved["data"]})
                self.state["saved_experience"] = saved["data"]
            except (ValueError, ValidationError, RuntimeError) as error:
                self.state["experience_error"] = f"{type(error).__name__}: {error}"
        self.finalize_state("finished")

    def finalize_state(self, phase):
        self.state.update(phase=phase, termination_reason=self.context.result["termination_reason"],
                          final_verdict=self.context.result["agent_verdict"])
        self.persist()

    def persist(self):
        self.state["step"] = self.context.result["actions"]
        self.context.result["agent_runtime"] = {
            "phase": self.state["phase"],
            "context_updates": self.state["context_updates"],
            "compacted_through_step": self.state["compacted_through_step"],
            "retrieved_experiences": len(self.state["retrieved_experiences"]),
            "external_tool_calls": self.state["external_tool_calls"],
            "finish_deferrals": self.state["finish_deferrals"],
            "memory_namespace": self.settings["memory_namespace"]}
        write_json(self.context.directory / "task-state.json", self.state)

    def screenshot(self):
        return self.context.directory / f"screenshots/{self.context.result['actions']:03d}.png"

    def __exit__(self, *exc):
        if exc[0] is not None:
            interrupted = isinstance(exc[1], KeyboardInterrupt)
            self.context.result["termination_reason"] = "interrupted" if interrupted else "infrastructure_failure"
            self.finalize_state("interrupted" if interrupted else "error")
        self.context.tools = None
        self.context.agent_session = None
        self.connections.__exit__(*exc)
