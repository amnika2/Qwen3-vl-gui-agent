import json
from contextlib import nullcontext
import time
from pathlib import Path

PROMPT = '''You operate a web browser using only screenshots, mouse and keyboard.
Return exactly one JSON action per response. Coordinates are normalized to [0,1000] on each axis.
Actions:
{"action":"click","coordinate":[x,y]}
{"action":"type","text":"text to insert into the focused field"}
{"action":"press","key":"Control+a"} (also Control+r, Enter, Tab, Escape, Backspace, Delete, arrows, Home, End, PageUp, PageDown)
{"action":"scroll","pixels":500} (positive means down)
{"action":"wait","seconds":1}
{"action":"finish","verdict":"pass|fail|blocked","observation":"what you saw","evidence_steps":[5]}
Typing inserts text; to replace, click the field, press Control+a, then type.
Use Control+r to reload the current page. Perform one action at a time.
Report product pass/fail only after saving and reopening the same record and inspecting the screenshot.
If execution could not be completed, report blocked. Cite existing screenshot steps.
'''

REVIEW_PROMPT = '''You judge a browser business test from screenshots and its executed action log.
The screenshots show the last typed state, the next action after typing when available, the state before a controller reload, and the state after reload, with step labels.
Action log entries are attempts, not proof of success: a type action does not prove the exact requested value was entered, and a Save click does not prove submission succeeded.
Inspect the visible values and validation messages. Report pass or fail only when all three facts are visibly established: the exact requested value was entered without appended or old text; submission succeeded without a validation error; and the same record or its final field/list row is visible after reopening.
Use fail only for a demonstrated persistence mismatch after a valid submission. If the value was never correctly entered, the last edit was not submitted, submission was rejected, or the final record cannot be established, report blocked rather than fail.
Return exactly one final JSON object, with no browser action:
{"action":"finish","verdict":"pass|fail|blocked","requested_value_entered":true,"submission_succeeded":true,"final_record_visible":true,"observation":"visible values, submission outcome and reasoning","evidence_steps":[5]}
Set each evidence field to true only when the corresponding fact is visibly established, otherwise false. A pass or fail with any false field will be guarded to blocked.
requested_value_entered describes only the typed-state screenshot: never set it false merely because the value differs after reopening; that later mismatch is exactly the fail condition.
A success toast proves only that some value was accepted, not that the requested value was correctly entered. If the final value has an old prefix/suffix, contains merged text, or is a partial form of the requested text, re-inspect the typed-state screenshot. When the typed state contains old or merged text, or the exact requested input cannot be visibly established, set requested_value_entered=false and report blocked even when submission_succeeded=true.
final_record_visible is true when the same record is identifiable in either its detail form or its list row. A list row showing the tested field is sufficient; do not report blocked merely because the detail form closed.
For list rows, use the post-save screenshot and field order to identify the tested field. A different field retaining an old value is not a persistence failure; do not mistake another phone number or value for the tested field.
When all three fields are true, verdict must be pass if the visible final value exactly matches the requested value, otherwise fail.
Cite the screenshot steps showing your evidence.
'''

BUSINESS_REVIEW_PROMPT = '''You judge a browser form task from screenshots and its executed action log.
Establish the exact requested input, the actual submit action, and the visible business outcome. Action attempts alone do not prove success.
For a normal save task, report pass when the same record is reopened with the requested values. Report fail for a demonstrated persistence mismatch, or for a task-specific business error that rejects correctly entered, valid input after the real submit.
When the task explicitly requires rejecting an invalid or duplicate value, report pass only when that intended rejection is visibly established, and fail only when the invalid record is actually saved. Explain the task-specific evidence.
Keep all evidence fields truthful. submission_succeeded is false when a save is rejected. final_record_visible is false when no resulting record is visible. These false fields do not by themselves make a demonstrated business rejection blocked.
submission_observed means the submit action completed and its result screen is observed. business_rejection_visible means the screenshots visibly show the relevant business error or validation rejection. expected_rejection reflects the task's stated expectation, not the build or an assumed bug label.
Report blocked if the exact input was not established, submission was not observed, the wrong object was used, or the outcome is ambiguous. An unrelated setup, login, loading or network failure is blocked. Do not infer a product defect from a generic error alone.
Return exactly one final JSON object:
{"action":"finish","verdict":"pass|fail|blocked","requested_value_entered":true,"submission_succeeded":false,"final_record_visible":false,"submission_observed":true,"business_rejection_visible":true,"expected_rejection":false,"observation":"visible input, submit response, same object or specific rejection, and why that outcome meets or violates the task","evidence_steps":[5]}
The example evidence values are illustrative; set every field from the screenshots and action log. For non-rejected saves, require the same reopened-record evidence as for a persistence check. Cite the screenshots supporting the input and the final outcome.
'''

REVIEW_PROMPTS = {"persistence": REVIEW_PROMPT, "business_outcome_v1": BUSINESS_REVIEW_PROMPT}
BUSINESS_OPERATION_PROMPT = PROMPT.replace(
    "Report product pass/fail only after saving and reopening the same record and inspecting the screenshot.",
    "After entering the requested values and submitting the real form, inspect the visible outcome. "
    "For accepted saves, reopen the same record and verify its values. For a task-specific business "
    "rejection, preserve and inspect the error screen. If the task requires rejecting an invalid or "
    "duplicate value, verify that rejection rather than claiming a record was created. "
    "Judge the outcome against the task and cite the input and outcome screenshots.")
OPERATION_PROMPTS = {"persistence": PROMPT, "business_outcome_v1": BUSINESS_OPERATION_PROMPT}


class Qwen:
    def __init__(self, config):
        self.config = config
        self.loaded = False
        self.load_seconds = 0
        self.review_tokenizer_data = None

    def load(self):
        if self.loaded:
            return
        import torch
        from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

        start = time.monotonic()
        torch.manual_seed(self.config["seed"])
        self.torch = torch
        location = self.config["model_path"]
        self.processor = AutoProcessor.from_pretrained(location, local_files_only=True,
                    min_pixels=256 * 28 * 28, max_pixels=self.config["max_pixels"])
        self.model = Qwen3VLForConditionalGeneration.from_pretrained(location,
                    dtype=torch.bfloat16, device_map={"": "cuda:0"}, attn_implementation="sdpa",
                    local_files_only=True).eval()
        if self.config.get("adapter_path"):
            from peft import PeftModel
            self.model = PeftModel.from_pretrained(self.model, self.config["adapter_path"],
                                                 local_files_only=True).eval()
        self.loaded = True
        self.load_seconds = time.monotonic() - start

    def begin_run(self):
        self.load()
        self.torch.cuda.reset_peak_memory_stats(0)

    def generate(self, task, screenshot: Path, history, remaining, progress=None, profile="persistence"):
        from PIL import Image

        self.load()
        prompt = OPERATION_PROMPTS[profile]
        if self.config.get("review_format") in {"evidence_v2", "evidence_facts_v1"}:
            prompt += ("When finishing, nominate up to five evidence_steps: the original baseline "
                       "of fields to preserve (include step 0 when visible there), the entered "
                       "values, the first Save/create response, and the final reopened record. "
                       "For automatic creation, include the screen before the trigger even when "
                       "no typing is needed.\n")
        if self.config.get("agent_runtime", {}).get("enabled"):
            prompt += (
                "Use Agent context below: it contains your task plan, older-action summary, "
                "reflections and recalled procedures. Recalled procedures are guidance; inspect "
                "the current screenshots for every value and outcome. Continue from next_goal. "
                "Browser actions use the original JSON format above. To use a listed MCP tool, "
                "return {\"action\":\"tool\",\"name\":\"server__tool\",\"arguments\":{...}}. "
                "Tool observations will be returned in Agent context. Procedural memory is "
                "recalled at startup and saved after review automatically.\n")
        messages = [{"role": "system", "content": [{"type": "text", "text": prompt}]}, {"role": "user", "content": [
            {"type": "text", "text": task + "\nRecent actions (oldest first):\n" + json.dumps(history[-self.config["history_actions"]:])
             + f"\nCurrent screenshot step: {history[-1]['step'] if history else 0}"
             + (("\nAgent context:\n" if self.config.get("agent_runtime", {}).get("enabled")
                 else "\nExecuted-action progress:\n") + json.dumps(progress, ensure_ascii=False)
                if progress is not None else "")},
            {"type": "image", "image": Image.open(screenshot).convert("RGB")}
        ]}]
        return self._generate(messages, remaining)

    def update_context(self, task, screenshot, previous, compacted, recent, reason, remaining):
        from PIL import Image
        from .agent_session import context_schema

        schema = context_schema(previous["plan"])
        plan_instruction = (
            "Return statuses, an array in exactly the existing plan's order. Preserve every plan "
            "step; only update its status. " if previous["plan"] else
            "Return plan, 3-5 short steps each with step and status. ")
        messages = [{"role": "system", "content": [{"type": "text", "text": (
            "Maintain the working memory and plan of a screenshot GUI agent. " + plan_instruction +
            "Status values are pending/in_progress/done. Also return summary, reflection and "
            "next_goal. On initial_plan, decompose this task including Save and reopened-result "
            "verification. On later updates, preserve important prior summary facts and add the "
            "actions being compacted, with step numbers. Keep exact task constraints and known "
            "input/save/reopen milestones. Action attempts are not proof of success. Use the current "
            "screenshot and feedback to update statuses, explain failed actions and choose a "
            "concrete next goal. Distinguish unestablished input from a product failure. "
            "Use 3-5 short plan steps. Mark established completed steps done, only the current "
            "step in_progress. Summary describes executed actions, never restates the task or "
            "speculates about outcomes. Initial summary and reflection are empty. For later updates "
            "write summary in 1-3 sentences, reflection in one sentence. Close each string promptly. "
            "An example summary is 'Step 1 opened the editor. Step 3 entered the requested value.' "
            "Recalled experiences provide procedures only; verify this run afresh.")}]},
            {"role": "user", "content": [{"type": "text", "text": json.dumps({
                "task": task, "reason": reason, "previous_state": previous,
                "actions_to_compact": compacted, "recent_actions": recent}, ensure_ascii=False)},
                {"type": "image", "image": Image.open(screenshot).convert("RGB")}]}]
        return self._generate(messages, remaining, max_new_tokens=512, schema=schema,
                              generation_options={"repetition_penalty": 1.1},
                              use_base=self.config["agent_runtime"].get("planner_adapter") == "base")

    def learn_episode(self, episode, remaining):
        from .agent_session import EXPERIENCE_SCHEMA

        messages = [{"role": "system", "content": [{"type": "text", "text": (
            "Extract reusable GUI interaction experience from this public episode. Return JSON "
            "with procedure and lessons, each a short list of concise instructions. Record how to "
            "locate controls, replace input, submit and reopen, and how to avoid observed mistakes. "
            "Do not store product verdicts, task-specific literal values, credentials, build/version "
            "labels or claims that a previous result predicts the next run. Failed episodes can "
            "supply cautions; do not describe an unsuccessful procedure as proven successful.")}]},
            {"role": "user", "content": [{"type": "text", "text": json.dumps(episode, ensure_ascii=False)}]}]
        return self._generate(messages, remaining, max_new_tokens=512, schema=EXPERIENCE_SCHEMA,
                              generation_options={"repetition_penalty": 1.1},
                              use_base=self.config["agent_runtime"].get("planner_adapter") == "base")

    def assess(self, task, screenshots, history, remaining, profile="persistence", frames=None):
        from PIL import Image

        if self.config.get("review_format") in {"evidence_v2", "evidence_facts_v1"}:
            from .review_context import review_content

            if self.config["review_format"] == "evidence_facts_v1":
                from .review_facts import facts_prompt as review_prompt, facts_schema as json_schema
            else:
                from .review_contract import json_schema, review_prompt

            paths = {step: Image.open(path).convert("RGB") for step, path in screenshots}
            return self._generate([
                {"role": "system", "content": [{"type": "text", "text": review_prompt(profile)}]},
                {"role": "user", "content": review_content(task, history, frames, paths)}], remaining,
                max_new_tokens=self.config.get("review_max_new_tokens"),
                schema=json_schema(list(paths)))
        if self.config.get("review_format") == "evidence_v1":
            from .review_evidence import review_content, review_prompt

            paths = {step: Image.open(path).convert("RGB") for step, path in screenshots}
            content = review_content(task, history, frames, paths)
            return self._generate([
                {"role": "system", "content": [{"type": "text", "text": review_prompt(profile)}]},
                {"role": "user", "content": content}], remaining,
                max_new_tokens=self.config.get("review_max_new_tokens"))
        content = [{"type": "text", "text": task + "\nExecuted actions:\n" + json.dumps(history)}]
        for step, screenshot in screenshots:
            content.extend([{"type": "text", "text": f"Screenshot step {step}"},
                            {"type": "image", "image": Image.open(screenshot).convert("RGB")}])
        return self._generate([{"role": "system", "content": [{"type": "text", "text": REVIEW_PROMPTS[profile]}]},
                               {"role": "user", "content": content}], remaining)

    def _generate(self, messages, remaining, max_new_tokens=None, schema=None, generation_options=None, use_base=False):
        inputs = self.processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                    return_dict=True, return_tensors="pt").to(self.model.device)
        options = dict(generation_options or {})
        if schema is not None:
            from lmformatenforcer import JsonSchemaParser
            from lmformatenforcer.integrations.transformers import (
                build_token_enforcer_tokenizer_data, build_transformers_prefix_allowed_tokens_fn)

            if self.review_tokenizer_data is None:
                self.review_tokenizer_data = build_token_enforcer_tokenizer_data(self.processor.tokenizer)
            options["prefix_allowed_tokens_fn"] = build_transformers_prefix_allowed_tokens_fn(
                self.review_tokenizer_data, JsonSchemaParser(schema))
        adapter_scope = self.model.disable_adapter() if use_base and self.config.get("adapter_path") else nullcontext()
        with adapter_scope, self.torch.inference_mode():
            outputs = self.model.generate(**inputs, do_sample=False,
                         max_new_tokens=max_new_tokens or self.config["max_new_tokens"],
                         max_time=max(0.1, remaining), **options)
        return self.processor.batch_decode(outputs[:, inputs.input_ids.shape[1]:], skip_special_tokens=True)[0]

    def metrics(self):
        if not self.loaded:
            return {"load_seconds": self.load_seconds}
        return {"load_seconds": self.load_seconds,
                "torch": self.torch.__version__, "device": self.torch.cuda.get_device_name(0),
                "peak_allocated_mib": self.torch.cuda.max_memory_allocated(0) / 1024 ** 2,
                "peak_reserved_mib": self.torch.cuda.max_memory_reserved(0) / 1024 ** 2}


class FakeModel:
    def __init__(self, responses):
        self.responses = iter(responses)

    def load(self):
        pass

    def begin_run(self):
        pass

    def generate(self, task, screenshot, history, remaining, progress=None, profile="persistence"):
        return next(self.responses)

    def assess(self, task, screenshots, history, remaining, profile="persistence", frames=None):
        return next(self.responses)

    def metrics(self):
        return {"synthetic": True}
