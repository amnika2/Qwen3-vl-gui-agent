import copy
from pathlib import Path

import pytest
import yaml

from guiqa.review_expansion import load_original, validate_partitions
from guiqa.sft_v2 import checksum


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def source():
    training, legacy, gold = load_original(ROOT)
    split = yaml.safe_load((ROOT / "configs/sft-split.yaml").read_text())
    return training, legacy, gold, split


def test_original_reviews_keep_content_separation_and_source_bytes(source):
    paths = [ROOT / "datasets/rl-review-pilot-v2" / name
             for name in ("train.jsonl", "validation.jsonl", "manifest.json")]
    before = {path: checksum(path) for path in paths}
    training, legacy, gold, split = source
    validate_partitions(training + legacy, gold, split)
    assert (len(training), len(legacy)) == (145, 39)
    assert {path: checksum(path) for path in paths} == before


@pytest.mark.parametrize("change_task_prose", [False, True])
def test_renaming_a_training_task_cannot_move_it_into_development(source, change_task_prose):
    training, _, gold, split = source
    clone = copy.deepcopy(training[0])
    clone.update(id="renamed-development", data_split="validation", scenario_id="new-name",
                 scenario_fingerprint="a-new-metadata-hash", parent_scenario_id="new-parent")
    clone["derived_provenance"]["source_run_id"] = "new-run"
    if change_task_prose:
        clone["messages"][1]["content"][0]["text"] = "Please complete this task. " + clone["messages"][1]["content"][0]["text"]
    with pytest.raises(ValueError, match="requirements crosses|public_task crosses"):
        validate_partitions([training[0], clone], {**gold, clone["id"]: gold[training[0]["id"]]}, split)


def test_paired_application_versions_stay_in_the_same_partition(source):
    training, _, gold, split = source
    clone = copy.deepcopy(training[0])
    clone.update(id="paired-version", build="B")
    clone["derived_provenance"]["source_run_id"] = "paired-version-run"
    validate_partitions([training[0], clone], {**gold, clone["id"]: gold[training[0]["id"]]}, split)
