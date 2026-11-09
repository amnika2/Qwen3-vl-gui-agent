"""Capture real unsubmitted expert prefixes with the inference review path."""
import argparse
import itertools
import json
import time
import uuid
from pathlib import Path

from guiqa import runner, sft_flows
from guiqa.artifacts import write_json
from guiqa.browser import execute
from guiqa.cli import load_config
from guiqa.control import LoopControl
from guiqa.oracle import in_view
from guiqa.sft_data import SftRecorder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append")
    parser.add_argument("--port", type=int, default=13223)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "datasets/sft-main-10apps-v1/manifest.json").read_text())
    selected = select_sources(manifest["accepted_runs"], args.case)
    batch = root / "runs" / ("unsubmitted-" + uuid.uuid4().hex[:8])
    batch.mkdir()
    config = load_config(root / "configs/agent.yaml")
    write_json(batch / "schedule.json", {"sources": selected, "config": config})
    completed = []
    for source in selected:
        scenario_path = root / "runs" / source["run_id"] / "private/scenario.json"
        scenario = json.loads(scenario_path.read_text()) if scenario_path.exists() else None
        _, fixture = runner.load_case(root, source["case_id"], source["workflow"])
        if scenario:
            fixture = {**fixture, **scenario["fixture"]}
        flow = sft_flows.FLOWS[source["case_id"]](fixture, source["build"])
        runner.agent = prefix_agent(flow, fixture)
        reviewer = PrefixReviewer()
        directory, result = runner.run_case(
            root, source["case_id"], source["build"], source["workflow"], "qwen-controlled",
            config, args.port, model=reviewer, experiment=batch.name, scenario=scenario)
        cell = {"source_run_id": source["run_id"], "run_id": directory.name,
                "path": str(directory.relative_to(root)), "data_split": source.get("data_split", "train"),
                "termination_reason": result["termination_reason"]}
        completed.append(cell)
        write_json(batch / "completed.json", completed)
        if result["termination_reason"] == "controlled_review":
            row = json.loads((directory / "sft/samples.jsonl").read_text())
            row.update(source_run_id=directory.name, data_split=source.get("data_split", "train"),
                       scenario_id=result.get("scenario_id", "legacy-" + source["run_id"]),
                       scenario_fingerprint=result.get("scenario_fingerprint", source["trajectory_fingerprint"]),
                       source_scenario_id=source.get("scenario_id", "legacy-" + source["run_id"]),
                       source_scenario_fingerprint=source.get("scenario_fingerprint", source["trajectory_fingerprint"]),
                       parent_run_id=source["run_id"], variant="unsubmitted_prefix")
            with (batch / "reviews.jsonl").open("a") as output:
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(json.dumps(cell), flush=True)
    print(json.dumps({"batch": str(batch), "reviews": str(batch / "reviews.jsonl")}), flush=True)


def select_sources(accepted, requested):
    cases = requested or sorted({x["case_id"] for x in accepted if x.get("review_profile", "persistence") == "persistence"})
    selected = []
    for case in cases:
        candidates = [x for x in accepted if x["case_id"] == case]
        training = [x for x in candidates if x.get("data_split", "train") == "train"]
        validation = [x for x in candidates if x.get("data_split") == "validation"]
        selected.extend([training[0], validation[0] if validation else training[1]])
    return selected


def prefix_agent(flow, fixture):
    def collect(context, task, model, config):
        control = LoopControl(context.directory / "screenshots/000.png")
        for make_action in itertools.islice(flow.steps, flow.input_step):
            action = make_action(context.page)
            if context.result["actions"] + 1 in flow.navigation_steps:
                with context.page.expect_navigation(wait_until="domcontentloaded"):
                    outcome = execute(context.page, action)
            else:
                outcome = execute(context.page, action)
            context.result["actions"] += 1
            screenshot = context.capture(action["action"], {**action, **outcome})
            control.record(action, context.result["actions"], screenshot)
        model.context, model.config, model.fixture = context, config, fixture
        model.entered = requested_input_visible(context.page, context.result["case_id"], fixture)
        context.event({"phase": "prefix_evidence", "latest_edit_step": context.result["actions"],
                       "requested_input_visible": model.entered, "final_submit_executed": False})
        runner.controlled_review(context, task, model, control, set(range(context.result["actions"] + 1)),
                                 time.monotonic() + config["wall_seconds"], "expert_unsubmitted_prefix")
    return collect


def requested_input_visible(page, case, fixture):
    if case in {"sweeper-034", "sweeper-053"}:
        wanted = {fixture["selector"]: fixture["stages"][-1]["content"]}
        if case == "sweeper-053":
            wanted["#subject"] = fixture["stages"][-1]["subject"]
    elif case == "sweeper-105":
        wanted = {f'input[name="{key}"]': fixture[key] for key in ("code", "name", "description")}
        if fixture.get("country_code"):
            wanted['select[name="new_zone[country_code]"]'] = fixture["country_code"]
        if fixture.get("city"):
            wanted['input[name="new_zone[city]"]'] = fixture["city"]
    elif case == "sweeper-125":
        wanted = {f"#input-modal-{key}": str(value) for key, value in fixture["values"].items()}
    elif case == "sweeper-142":
        wanted = {f"input[name={key}]": value for key, value in fixture["changes"].items()}
    elif case == "sweeper-150":
        wanted = {fixture["selector"]: fixture["input"]}
    elif case == "sweeper-185":
        wanted = {"input[name=value]": fixture["input"]}
    return all(in_view(page.locator(selector)) and page.locator(selector).get_attribute("type") != "password"
               and page.locator(selector).input_value() == value for selector, value in wanted.items())


class PrefixReviewer:
    def assess(self, task, screenshots, history, remaining):
        answer = {"action": "finish", "verdict": "blocked", "requested_value_entered": self.entered,
                  "submission_succeeded": False,
                  "final_record_visible": existing_record_visible(self.context.page, self.context.result["case_id"], self.fixture),
                  "observation": ("The latest requested edit has not been submitted. The screenshots show the "
                                  "editing state and the controller reload, so a saved outcome cannot be judged. "
                                  + ("The requested input is visible before reload." if self.entered else
                                     "The complete requested input is not visibly established in this evidence window.")),
                  "evidence_steps": [step for step, _ in screenshots]}
        SftRecorder(self.context, task, self.config).final_review(answer["evidence_steps"], answer)
        return json.dumps(answer)

    def metrics(self):
        return {"synthetic": True, "expert_prefix_replay": True}


def existing_record_visible(page, case, fixture):
    if case in {"sweeper-034", "sweeper-053", "sweeper-150"}:
        return in_view(page.locator(fixture["selector"]))
    if case == "sweeper-185":
        return in_view(page.locator("input[name=value]"))
    if case == "sweeper-105":
        return bool(fixture.get("initial")) and in_view(page.locator('input[name="name"]'))
    if case == "sweeper-125":
        from guiqa.apps.impress_opencart import option_row
        return in_view(option_row(page, fixture))
    if case == "sweeper-142":
        return in_view(page.locator(f".edit_ipaddress[data-id='{fixture['object_id']}']"))


if __name__ == "__main__":
    main()
