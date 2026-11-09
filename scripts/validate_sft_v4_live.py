"""Run one validation scenario through the screenshot agent and independent oracle."""

import argparse
import hashlib
import json
from pathlib import Path

from collect_field_contrast import field_scenarios
from guiqa.artifacts import write_json
from guiqa.cli import load_config
from guiqa.model import Qwen
from guiqa.runner import run_case


def main():
    args = arguments()
    root = Path(__file__).resolve().parents[1]
    catalog = root / "configs/sft-field-contrast-scenarios.yaml"
    scene = validation_scene(catalog, args.scenario)
    config = load_config(args.config.resolve())
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    model = Qwen(config)
    directory, result = run_case(
        root, scene["case_id"], scene["build"], scene["workflow"], "qwen-controlled", config,
        port=args.port, model=model, experiment=output.name, scenario=scene)
    oracle_matches = result["oracle_verdict"] == result["matrix_expected"]
    agent_matches = result["agent_verdict"] == result["oracle_verdict"]
    summary = {
        "scenario_id": scene["id"], "data_split": scene["data_split"],
        "run_path": str(directory.relative_to(root)), "result": result,
        "oracle_matches_expected": oracle_matches, "agent_matches_oracle": agent_matches,
        "passed": oracle_matches and agent_matches,
        "config_path": str(args.config.resolve()), "config": config,
        "catalog_sha256": hashlib.sha256(catalog.read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    write_json(output / "result.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if summary["passed"] else 1


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def validation_scene(catalog, identifier):
    scenes = field_scenarios(catalog)
    scene = next((scene for scene in scenes if scene["id"] == identifier), None)
    if scene is None or scene["data_split"] != "validation":
        raise ValueError("scenario must name a validation entry in the field-contrast catalog")
    return scene


if __name__ == "__main__":
    raise SystemExit(main())
