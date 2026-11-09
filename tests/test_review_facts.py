import copy
import json
from pathlib import Path

import pytest

from guiqa.review_contract import review_guard
from guiqa.review_facts import facts_prompt, facts_schema, parse_facts, render_facts, to_facts


ROOT = Path(__file__).resolve().parents[1]


def test_all_source_reviews_keep_the_same_facts_and_verdict_without_free_explanation():
    for partition in ("train", "validation"):
        rows = (ROOT / "datasets/rl-review-pilot-v2" / f"{partition}.jsonl").read_text().splitlines()
        for line in rows:
            row = json.loads(line)
            gold = json.loads(row["messages"][-1]["content"])
            original = copy.deepcopy(gold)
            compact = to_facts(gold)
            steps = {frame["step"] for frame in row["review_frames"]}

            rendered = parse_facts(json.dumps(compact), steps, row["review_profile"])

            assert gold == original
            assert to_facts(rendered) == compact
            assert review_guard(rendered, row["review_profile"]) is None
            assert len(rendered["observation"]) <= 512
            assert "Save" not in rendered["observation"]
            assert "Cancel" not in rendered["observation"]


def test_schema_and_prompt_request_facts_without_explanation():
    schema = facts_schema([3, 7])
    assert "observation" not in schema["properties"]
    evidence = schema["properties"]["submission"]["properties"]["evidence"]["anyOf"][1]
    assert evidence["required"] == ["step"]
    assert evidence["properties"]["step"]["enum"] == [3, 7]
    assert "A visibly empty field has value=''" in facts_prompt()
    assert "generic program exception" in facts_prompt()


def test_model_cannot_insert_free_explanation_into_compact_protocol():
    row = json.loads((ROOT / "datasets/rl-review-pilot-v2/train.jsonl").read_text().splitlines()[0])
    facts = to_facts(json.loads(row["messages"][-1]["content"]))
    steps = {frame["step"] for frame in row["review_frames"]}
    facts["observation"] = "A fabricated explanation."
    with pytest.raises(ValueError, match="facts only"):
        parse_facts(json.dumps(facts), steps, row["review_profile"])


def test_rendering_distinguishes_empty_unseen_and_mismatching_values():
    facts = {
        "submission": {"status": "unknown", "evidence": None},
        "result_observed": False, "verdict": "blocked",
        "field_readings": [{"result_matches_request": None}, {"result_matches_request": False}],
    }
    answer = render_facts(facts)
    assert "0 matching, 1 mismatching, 1 unestablished" in answer["observation"]
    assert answer["submission"]["evidence"] is None
