import json

import pytest

from guiqa.dataset import parse_dataset


def row():
    return {"case_id": "sweeper-001", "slug": "x", "repo": "https://example.test/repo",
            "base_commit": "a" * 40, "reference_commit": "b" * 40,
            "task_scoped": "a", "task_specified": "b",
            "workflows": "workflows:\n- category: preservation\n  steps: [check]\n- category: target\n  steps: [edit]\n"}


def test_categories_not_order_dependent():
    assert len(parse_dataset(json.dumps(row()).encode())) == 1


def test_missing_preservation_rejected():
    value = row()
    value["workflows"] = "workflows:\n- category: target\n  steps: [edit]\n"
    with pytest.raises(ValueError):
        parse_dataset(json.dumps(value).encode())
