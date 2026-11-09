import json
from pathlib import Path
import sys
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from playwright.sync_api import sync_playwright

from guiqa.actions import parse_action
from guiqa.agent_memory import ExperienceStore
from guiqa.agent_session import AgentSession, context_schema
from guiqa.agent_tools import MCPConnections, ToolRegistry
from guiqa.cli import load_config
from guiqa.model import FakeModel, Qwen
from guiqa.runner import RunContext, agent


def memory_server(database, namespace="test"):
    return {"name": "memory", "command": sys.executable,
            "args": ["-m", "guiqa.mcp_server", "--database", str(database), "--namespace", namespace]}


def test_memory_survives_restart_and_keeps_namespaces_separate(tmp_path):
    database = tmp_path / "memory.sqlite3"
    ExperienceStore(database).save("interactive", "008", "target", "Edit a site variable",
        ["Open the variable, replace Value, save and reopen"], ["Check the reopened value"], "first-run")
    restarted = ExperienceStore(database)
    assert restarted.search("frozen-evaluation", "Edit a site variable", "008", "target")["experiences"] == []
    recalled = restarted.search("interactive", "Create a site variable", "008", "preservation")["experiences"]
    assert len(recalled) == 1
    assert recalled[0]["source_run_id"] == "first-run"
    assert "task" not in recalled[0]
    assert recalled[0]["procedure"] == ["Open the variable, replace Value, save and reopen"]


def test_real_mcp_discovery_write_restart_and_read(tmp_path):
    server = memory_server(tmp_path / "memory.sqlite3")
    with MCPConnections([server]) as tools:
        assert set(tools.catalog) == {"memory__search_experiences", "memory__save_experience"}
        saved = tools.call("memory__save_experience", {
            "case_id": "008", "workflow": "target", "task": "Edit a variable",
            "procedure": ["Select all before replacing"], "lessons": ["Reopen after Save"],
            "source_run_id": "episode-one"})
        assert not saved["is_error"]
        assert saved["data"]["experience_id"] == 1
        catalog = tools.catalog
    with MCPConnections([server]) as restarted:
        result = restarted.call("memory__search_experiences", {
            "task": "Create another variable", "case_id": "008", "workflow": "preservation"})
        assert result["data"]["experiences"][0]["source_run_id"] == "episode-one"
    with pytest.raises(ValueError, match="minimum"):
        parse_action(json.dumps({"action": "tool", "name": "memory__search_experiences", "arguments": {
            "task": "Edit a variable", "case_id": "008", "workflow": "target", "limit": 0}}),
            {0}, tools=catalog)
    with pytest.raises(ValueError, match="available tool"):
        parse_action('{"action":"tool","name":"missing","arguments":{}}', {0}, tools=catalog)


class ContextModel(FakeModel):
    def __init__(self, responses):
        super().__init__(responses)
        self.progress = []
        self.reasons = []
        self.compacted = []

    def generate(self, task, screenshot, history, remaining, progress=None, **kwargs):
        self.progress.append(json.loads(json.dumps({"history": history[-2:], "progress": progress})))
        return super().generate(task, screenshot, history, remaining, progress=progress, **kwargs)

    def update_context(self, task, screenshot, previous, compacted, recent, reason, remaining):
        self.reasons.append(reason)
        self.compacted.extend(compacted)
        entered = [row["action"]["text"] for row in self.compacted
                   if row.get("action") and row["action"]["action"] == "type"]
        update = {"summary": "Earlier typed values: " + repr(entered),
                  "reflection": reason, "next_goal": "Inspect the current page"}
        if previous["plan"]:
            update["statuses"] = ["done" if entered else "in_progress", "pending"]
        else:
            update.update(plan=[{"step": "Enter requested value", "status": "in_progress"},
                                {"step": "Save and reopen", "status": "pending"}], summary="", reflection="")
        return json.dumps(update)

    def learn_episode(self, episode, remaining):
        assert "PRIVATE_ORACLE" not in json.dumps(episode)
        assert "Important earlier target" not in json.dumps(episode)
        assert "task" not in episode
        return json.dumps({"procedure": ["Focus the field, select all, replace, Save, then reopen"],
                           "lessons": ["Verify the current saved value rather than reusing a previous result"]})


@pytest.fixture
def runtime_episode(tmp_path):
    class Observer:
        def capture(self, step, screenshot):
            pass

    config = {"wall_seconds": 60, "max_actions": 30, "history_actions": 4,
              "agent_runtime": {"enabled": True, "refresh_actions": 3,
                                "memory_database": str(tmp_path / "memory.sqlite3"),
                                "memory_namespace": "test", "memory_top_k": 3, "max_tool_calls": 3}}
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.route("http://guiqa.test/", lambda route: route.fulfill(content_type="text/html", body='''
<input id="value" style="position:absolute;left:150px;top:75px;width:300px">
<button style="position:absolute;left:150px;top:160px;width:300px;height:50px"
 onclick="localStorage.setItem('saved', document.getElementById('value').value)">Save</button>
<script>document.getElementById('value').value = localStorage.getItem('saved') || 'Old';</script>'''))
            page.goto("http://guiqa.test/")
            result = dict(run_id="test-runtime", case_id="synthetic", workflow="target", method="qwen-controlled",
                          actions=0, model_calls=0, agent_verdict="blocked", termination_reason="interrupted")
            context = RunContext(tmp_path, result, Observer(), page)
            context.capture("initial")
            yield context, config


def test_unfinished_plan_continues_before_reloading_unsaved_form(runtime_episode):
    context, config = runtime_episode

    class SaveModel(ContextModel):
        def update_context(self, *args, **kwargs):
            raw = super().update_context(*args, **kwargs)
            update = json.loads(raw)
            if "statuses" in update:
                saved = context.page.evaluate("localStorage.getItem('saved')") == "New"
                update["statuses"] = ["done", "done" if saved else "pending"]
                update["next_goal"] = "Finish" if saved else "Click Save"
            return json.dumps(update)

    actions = [{"action": "click", "coordinate": [200, 100]},
               {"action": "press", "key": "Control+a"}, {"action": "type", "text": "New"},
               {"action": "finish", "verdict": "fail", "observation": "Old value remains", "evidence_steps": [3]},
               {"action": "click", "coordinate": [200, 200]},
               {"action": "finish", "verdict": "pass", "observation": "Saved New", "evidence_steps": [4]},
               {"action": "finish", "verdict": "pass", "observation": "Reopened New", "evidence_steps": [5],
                "requested_value_entered": True, "submission_succeeded": True, "final_record_visible": True}]
    model = SaveModel([json.dumps(action) for action in actions])
    agent(context, "Replace Old with New, save and reopen", model, config)
    assert context.page.locator("#value").input_value() == "New"
    assert context.result["agent_verdict"] == "pass"
    assert context.result["agent_runtime"]["finish_deferrals"] == 1
    trace = [json.loads(line) for line in (context.directory / "trace.jsonl").read_text().splitlines()]
    assert trace[4]["action"]["action"] == "click"
    assert trace[5]["action"]["key"] == "Control+r"
    events = [json.loads(line) for line in (context.directory / "model.jsonl").read_text().splitlines()]
    assert len([event for event in events if event.get("phase") == "finish_deferred"]) == 1


def test_execution_timeout_triggers_reflection_then_allows_blocked_exit(runtime_episode, monkeypatch):
    from playwright.sync_api import TimeoutError

    context, config = runtime_episode
    execute = context.execute

    def timeout_on_click(action, caller="agent"):
        if action["action"] == "click":
            raise TimeoutError("The button did not become ready")
        return execute(action, caller)

    monkeypatch.setattr(context, "execute", timeout_on_click)
    model = ContextModel([json.dumps(action) for action in [
        {"action": "click", "coordinate": [200, 200]},
        {"action": "finish", "verdict": "blocked", "observation": "Save is unavailable", "evidence_steps": [1]},
        {"action": "finish", "verdict": "blocked", "observation": "Save is unavailable", "evidence_steps": [2]}]])
    agent(context, "Save a value", model, config)
    assert any(reason.startswith("execution_error: The button") for reason in model.reasons)
    assert context.result["agent_verdict"] == "blocked"
    assert context.result["agent_runtime"]["finish_deferrals"] == 0
    assert context.result["agent_runtime"]["phase"] == "finished"


def test_unfinished_plan_only_defers_finish_once(runtime_episode):
    context, config = runtime_episode
    finish = {"action": "finish", "verdict": "fail", "observation": "Save did not succeed", "evidence_steps": [0]}
    review = {"action": "finish", "verdict": "blocked", "observation": "Submission was not established", "evidence_steps": [1]}
    model = ContextModel([json.dumps(action) for action in [finish, finish, review]])
    agent(context, "Save and inspect a value", model, config)
    assert context.result["termination_reason"] == "controlled_review"
    assert context.result["agent_runtime"]["finish_deferrals"] == 1
    assert context.result["actions"] == 1
    assert len([reason for reason in model.reasons if reason.startswith("finish_requested")]) == 1


def test_action_budget_has_a_stopped_state_without_learning(runtime_episode):
    context, config = runtime_episode
    context.result["method"] = "qwen-base"
    config["max_actions"] = 1
    click = json.dumps({"action": "click", "coordinate": [200, 100]})
    model = ContextModel([click, click])
    agent(context, "Save and inspect a value", model, config)
    state = json.loads((context.directory / "task-state.json").read_text())
    assert state["phase"] == context.result["agent_runtime"]["phase"] == "stopped"
    assert state["termination_reason"] == "action_budget"
    assert context.result["actions"] == 1
    assert not (context.directory / "experience.json").exists()


@pytest.mark.parametrize("error,phase,reason", [(KeyboardInterrupt(), "interrupted", "interrupted"),
                                               (RuntimeError("generation failed"), "error", "infrastructure_failure")])
def test_interrupted_and_failed_tasks_persist_terminal_state(runtime_episode, error, phase, reason):
    context, config = runtime_episode

    class FailingModel(ContextModel):
        def generate(self, *args, **kwargs):
            raise error

    with pytest.raises(type(error)):
        agent(context, "Save a value", FailingModel([]), config)
    state = json.loads((context.directory / "task-state.json").read_text())
    assert state["phase"] == context.result["agent_runtime"]["phase"] == phase
    assert state["termination_reason"] == context.result["termination_reason"] == reason
    assert state["final_verdict"] == "blocked"
    assert context.tools is context.agent_session is None


def test_wall_budget_stops_without_post_budget_context_or_learning(runtime_episode, monkeypatch):
    import guiqa.agent_session as session_module
    import guiqa.runner as runner_module

    context, config = runtime_episode
    clock = SimpleNamespace(now=0)
    time_source = SimpleNamespace(monotonic=lambda: clock.now)
    monkeypatch.setattr(session_module, "time", time_source)
    monkeypatch.setattr(runner_module, "time", time_source)

    class ExpiringModel(ContextModel):
        def update_context(self, *args, **kwargs):
            raw = super().update_context(*args, **kwargs)
            clock.now = 61
            return raw

        def learn_episode(self, *args, **kwargs):
            pytest.fail("learning must not run after the task budget")

    model = ExpiringModel([])
    agent(context, "Save a value", model, config)
    state = json.loads((context.directory / "task-state.json").read_text())
    assert state["phase"] == context.result["agent_runtime"]["phase"] == "stopped"
    assert state["termination_reason"] == "wall_clock_budget"
    assert context.result["model_calls"] == 1
    assert model.reasons == ["initial_plan"]


def test_optional_context_and_experience_errors_preserve_task_result(runtime_episode):
    context, config = runtime_episode

    class BrokenFinalizationModel(ContextModel):
        def update_context(self, *args, **kwargs):
            if args[5].startswith("final_review_completed"):
                return "unfinished JSON"
            return super().update_context(*args, **kwargs)

        def learn_episode(self, episode, remaining):
            raise RuntimeError("summary generation failed")

    model = BrokenFinalizationModel([json.dumps(action) for action in [
        {"action": "click", "coordinate": [200, 100]},
        {"action": "finish", "verdict": "blocked", "observation": "No change needed", "evidence_steps": [1]},
        {"action": "finish", "verdict": "pass", "observation": "Saved value is present", "evidence_steps": [2],
         "requested_value_entered": True, "submission_succeeded": True, "final_record_visible": True}]])
    agent(context, "Inspect the saved value", model, config)
    state = json.loads((context.directory / "task-state.json").read_text())
    assert state["phase"] == context.result["agent_runtime"]["phase"] == "finished"
    assert context.result["agent_verdict"] == "pass"
    assert context.result["termination_reason"] == "controlled_review"
    assert "JSONDecodeError" in state["context_error"]
    assert "summary generation failed" in state["experience_error"]


def test_browser_context_compaction_mcp_feedback_and_final_memory(tmp_path):
    class Observer:
        def capture(self, step, screenshot):
            pass

    database = tmp_path / "memory.sqlite3"
    ExperienceStore(database).save("test", "synthetic", "target", "Replace a field",
                                 ["Select all before typing"], ["Reopen to verify"], "previous-episode")
    lookup = {"action": "tool", "name": "memory__search_experiences", "arguments": {
        "task": "Replace a field", "case_id": "synthetic", "workflow": "target"}}
    actions = [lookup, {"action": "click", "coordinate": [200, 100]},
               {"action": "press", "key": "Control+a"},
               {"action": "type", "text": "Important earlier target"},
               *[{"action": "press", "key": "ArrowRight"}] * 3,
               {"action": "finish", "verdict": "blocked", "observation": "No Save exists", "evidence_steps": [6]},
               {"action": "finish", "verdict": "blocked", "observation": "No Save exists", "evidence_steps": [7]}]
    model = ContextModel([json.dumps(action) for action in actions])
    config = {"wall_seconds": 60, "max_actions": 30, "history_actions": 2,
              "agent_runtime": {"enabled": True, "refresh_actions": 2, "memory_database": str(database),
                                "memory_namespace": "test", "memory_top_k": 3, "max_tool_calls": 3}}
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.route("http://guiqa.test/", lambda route: route.fulfill(
                body='<input value="Old" style="position:absolute;left:150px;top:75px;width:300px">'))
            page.goto("http://guiqa.test/")
            result = dict(run_id="new-episode", case_id="synthetic", workflow="target", method="qwen-controlled",
                          actions=0, model_calls=0, agent_verdict="blocked", actual="PRIVATE_ORACLE")
            context = RunContext(tmp_path, result, Observer(), page)
            context.capture("initial")
            agent(context, "Replace a field and Save", model, config)
    state = json.loads((tmp_path / "task-state.json").read_text())
    assert state["phase"] == "finished"
    assert [step["step"] for step in state["plan"]] == ["Enter requested value", "Save and reopen"]
    assert state["compacted_through_step"] >= 3
    assert "Important earlier target" in state["summary"]
    assert any("action_cycle" in reason for reason in model.reasons)
    assert "tool_result: memory__search_experiences" in model.reasons
    assert any(item["progress"]["tool_observations"] for item in model.progress)
    assert any("Important earlier target" in item["progress"]["task_state"]["summary"]
               and not any(row.get("action", {}).get("action") == "type" for row in item["history"])
               for item in model.progress if all(row.get("action") for row in item["history"]))
    calls = [json.loads(line) for line in (tmp_path / "tool-calls.jsonl").read_text().splitlines()]
    assert any(row["name"].startswith("browser__") for row in calls)
    assert any(row["name"] == "memory__search_experiences" and row["caller"] == "agent" for row in calls)
    assert result["agent_runtime"]["retrieved_experiences"] == 1
    assert result["agent_runtime"]["external_tool_calls"] == 1
    recalled = ExperienceStore(database).search("test", "Replace another field", "synthetic", "target")
    assert {row["source_run_id"] for row in recalled["experiences"]} == {"previous-episode", "new-episode"}


def test_model_prompt_contains_summary_plan_and_discovered_tools(tmp_path):
    from PIL import Image

    image = tmp_path / "screen.png"
    Image.new("RGB", (8, 8), "white").save(image)
    model = Qwen({"agent_runtime": {"enabled": True}, "history_actions": 2})
    model.load = lambda: None
    captured = []
    model._generate = lambda messages, *args, **kwargs: captured.append(messages) or "response"
    model.generate("Update a variable", image, [], 30,
                   progress={"summary": "Earlier Save was accepted", "tools": [{"name": "memory__search_experiences"}]})
    text = "\n".join(block["text"] for message in captured[0] for block in message["content"] if block["type"] == "text")
    assert "Earlier Save was accepted" in text
    assert '"action":"tool"' in text
    assert "memory__search_experiences" in text


def test_browser_tool_format_uses_existing_action_validation(tmp_path):
    class Connections:
        catalog = {}

    registry = ToolRegistry(None, tmp_path, Connections())
    action = parse_action(json.dumps({"action": "tool", "name": "browser__click",
                                      "arguments": {"coordinate": [180, 300]}}), {0}, tools=registry.catalog)
    assert action == {"action": "click", "coordinate": [180, 300]}
    with pytest.raises(ValueError, match="coordinate"):
        parse_action(json.dumps({"action": "tool", "name": "browser__click",
                                 "arguments": {"coordinate": [180, 1001]}}), {0}, tools=registry.catalog)


def test_external_mcp_tool_error_is_returned_to_the_caller(tmp_path):
    server = tmp_path / "external.py"
    server.write_text('''from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
server = MCPServer("external-demo")
@server.tool()
def lookup_hint(topic: str) -> str:
    if topic != "replace":
        raise ToolError("Unknown topic; use replace")
    return "Select all before replacing text"
server.run()
''')
    with MCPConnections([{"name": "external", "command": sys.executable, "args": [str(server)]}]) as tools:
        assert "external__lookup_hint" in tools.catalog
        failed = tools.call("external__lookup_hint", {"topic": "missing"})
        assert failed["is_error"]
        assert "Unknown topic" in failed["text"]
        corrected = tools.call("external__lookup_hint", {"topic": "replace"})
        assert not corrected["is_error"]
        assert corrected["text"] == "Select all before replacing text"


def test_full_config_resolves_persistent_database():
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs/agent-full-grpo.yaml")
    assert config["agent_runtime"]["memory_database"] == str(root / ".runtime/agent-memory.sqlite3")
    assert config["adapter_path"].endswith("rl-review-grpo-expanded-v1/adapter")


def test_planning_off_keeps_summary_updates_and_removes_plan_from_model(runtime_episode):
    context, config = runtime_episode
    config["agent_runtime"]["planning"] = False

    class SummaryModel(ContextModel):
        def update_context(self, task, screenshot, previous, compacted, recent, reason, remaining):
            assert "plan" not in previous and "next_goal" not in previous
            self.reasons.append(reason)
            return json.dumps({"summary": "" if reason == "initial_plan" else "The field was edited at step 3",
                               "reflection": "" if reason == "initial_plan" else "Inspect the submitted value"})

    actions = [{"action": "click", "coordinate": [200, 100]}, {"action": "press", "key": "Control+a"},
               {"action": "type", "text": "New"},
               {"action": "finish", "verdict": "fail", "observation": "Value not saved", "evidence_steps": [3]},
               {"action": "finish", "verdict": "blocked", "observation": "No submission established", "evidence_steps": [4]}]
    model = SummaryModel([json.dumps(action) for action in actions])
    agent(context, "Edit and save a field", model, config)
    state = json.loads((context.directory / "task-state.json").read_text())
    assert state["summary"] == "The field was edited at step 3"
    assert "periodic_context_update" in model.reasons
    assert context.result["agent_runtime"]["finish_deferrals"] == 0
    assert all("plan" not in row["progress"]["task_state"]
               and "next_goal" not in row["progress"]["task_state"] for row in model.progress)


def test_memory_off_uses_browser_tools_without_creating_or_saving_memory(runtime_episode):
    context, config = runtime_episode
    config["agent_runtime"]["memory"] = False

    class NoMemoryModel(ContextModel):
        def learn_episode(self, *args):
            pytest.fail("memory-disabled tasks must not learn or save experiences")

    model = NoMemoryModel([json.dumps(action) for action in [
        {"action": "click", "coordinate": [200, 100]},
        {"action": "finish", "verdict": "blocked", "observation": "Cannot submit", "evidence_steps": [1]},
        {"action": "finish", "verdict": "blocked", "observation": "Cannot submit", "evidence_steps": [2]}]])
    agent(context, "Edit and save a field", model, config)
    catalog = json.loads((context.directory / "tool-catalog.json").read_text())["tools"]
    assert catalog and all(tool["transport"] == "browser" for tool in catalog)
    assert not Path(config["agent_runtime"]["memory_database"]).exists()
    assert not (context.directory / "experience.json").exists()
    assert all("retrieved_experiences" not in row["progress"]["task_state"] for row in model.progress)


def test_reflection_off_preserves_raw_errors_and_periodic_summary(runtime_episode, monkeypatch):
    from playwright.sync_api import TimeoutError

    context, config = runtime_episode
    config["agent_runtime"].update(reflection=False, refresh_actions=2)
    execute = context.execute

    def timeout_on_click(action, caller="agent"):
        if action["action"] == "click":
            raise TimeoutError("Save did not become ready")
        return execute(action, caller)

    class NoReflectionModel(ContextModel):
        def update_context(self, *args):
            update = json.loads(super().update_context(*args))
            del update["reflection"]
            return json.dumps(update)

    monkeypatch.setattr(context, "execute", timeout_on_click)
    actions = [{"action": "click", "coordinate": [200, 200]},
               *[{"action": "press", "key": "ArrowRight"}] * 3,
               {"action": "finish", "verdict": "blocked", "observation": "Cannot save", "evidence_steps": [4]},
               {"action": "finish", "verdict": "blocked", "observation": "Cannot save", "evidence_steps": [5]}]
    model = NoReflectionModel([json.dumps(action) for action in actions])
    agent(context, "Edit and save a field", model, config)
    assert "periodic_context_update" in model.reasons
    assert not any(reason.startswith(("execution_error", "action_cycle")) for reason in model.reasons)
    assert all("reflection" not in row["progress"]["task_state"] for row in model.progress)
    assert any("Save did not become ready" in json.dumps(row["history"]) for row in model.progress)


def test_reflection_off_keeps_raw_tool_error_without_extra_update(runtime_episode):
    context, config = runtime_episode
    config["agent_runtime"].update(memory=False, reflection=False)

    class NoReflectionModel(ContextModel):
        def update_context(self, *args):
            update = json.loads(super().update_context(*args))
            del update["reflection"]
            return json.dumps(update)

    model = NoReflectionModel([])
    with AgentSession(context, "Inspect the page", model, config, 10 ** 12) as session:
        session.start()
        session.tools.call = lambda *args: {"is_error": True, "text": "Unknown record", "data": None}
        session.call_tool({"name": "helper__lookup", "arguments": {"record": "missing"}})
        assert session.progress()["tool_observations"][-1]["result"]["text"] == "Unknown record"
        assert model.reasons == ["initial_plan"]


@pytest.mark.parametrize("disabled", ["planning", "reflection", "memory"])
def test_ablation_prompts_and_schema_only_request_enabled_features(tmp_path, disabled):
    from PIL import Image

    screenshot = tmp_path / "screen.png"
    Image.new("RGB", (8, 8), "white").save(screenshot)
    settings = {"enabled": True, disabled: False}
    model = Qwen({"agent_runtime": settings, "history_actions": 4})
    model.load = lambda: None
    captured = []
    model._generate = lambda messages, *args, **kwargs: captured.append((messages, kwargs)) or "{}"
    previous = {"summary": "", "plan": [], "reflection": "", "next_goal": "Open the form"}
    if disabled == "planning":
        del previous["plan"]
        del previous["next_goal"]
    elif disabled == "reflection":
        del previous["reflection"]
    model.update_context("Edit a field", screenshot, previous, [], [], "initial_plan", 30)
    context_prompt = captured[-1][0][0]["content"][0]["text"]
    schema = captured[-1][1]["schema"]
    if disabled == "planning":
        assert not {"plan", "statuses", "next_goal"} & schema["properties"].keys()
        assert "Return plan" not in context_prompt and "next_goal" not in context_prompt
        periodic = context_schema([], planning=False, initial=False)
        assert periodic["properties"]["summary"]["type"] == "string"
    elif disabled == "reflection":
        assert "reflection" not in schema["properties"]
        assert "reflection" not in context_prompt
    else:
        assert "Recalled experiences" not in context_prompt
    model.generate("Edit a field", screenshot, [], 30, progress={"task_state": previous})
    actor_prompt = captured[-1][0][0]["content"][0]["text"]
    assert {"planning": "next_goal", "reflection": "reflections", "memory": "recalled procedures"}[disabled] not in actor_prompt


def test_generation_usage_accumulates_all_calls_and_resets_each_run():
    model = Qwen({"max_new_tokens": 20})
    inputs = MagicMock()
    inputs.to.return_value = inputs
    inputs.input_ids.shape = (1, 7)
    outputs = MagicMock()
    outputs.__getitem__.return_value.shape = (1, 3)
    model.processor = SimpleNamespace(apply_chat_template=lambda *args, **kwargs: inputs,
                                      batch_decode=lambda *args, **kwargs: ["answer"])
    model.model = SimpleNamespace(device="cpu", generate=lambda **kwargs: outputs)
    model.torch = SimpleNamespace(inference_mode=nullcontext,
                                  cuda=SimpleNamespace(reset_peak_memory_stats=Mock()))
    model.load = lambda: None
    model.begin_run()
    model._generate([], 30)
    model._generate([], 30)
    metrics = model.metrics()
    assert metrics["prompt_tokens"] == 14
    assert metrics["completion_tokens"] == 6
    assert metrics["generation_calls"] == 2
    assert metrics["generation_seconds"] >= 0
    model.begin_run()
    assert model.metrics()["prompt_tokens"] == model.metrics()["completion_tokens"] == 0
    assert model.metrics()["generation_calls"] == 0
