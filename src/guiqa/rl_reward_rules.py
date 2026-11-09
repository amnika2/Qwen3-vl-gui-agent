"""Load screenshot-bound calibration rules for review rewards."""

import hashlib
from pathlib import Path

import yaml


def load_reward_rules(path):
    catalog = yaml.safe_load(Path(path).read_text())
    _validate_catalog(catalog)
    return catalog


def rules_for_sample(catalog, row, dataset_root):
    aliases = catalog["case_aliases"].get(row["case_id"], {})
    sample = catalog["samples"].get(_source_run_id(row))
    if sample is None:
        return {"field_aliases": dict(aliases)}
    _validate_binding(sample, row)
    _verify_evidence_images(sample, row, Path(dataset_root))
    return {**sample,
            "field_aliases": {**aliases, **sample.get("field_aliases", {})}}


def _validate_catalog(catalog):
    if not isinstance(catalog, dict) or catalog.get("version") != 1:
        raise ValueError("reward rules require version 1")
    for key in ("case_aliases", "samples"):
        if not isinstance(catalog.get(key), dict):
            raise ValueError("reward rules require a " + key + " mapping")
    for aliases in catalog["case_aliases"].values():
        _validate_aliases(aliases)
    for run_id, sample in catalog["samples"].items():
        if (not isinstance(sample, dict)
                or not {"case_id", "sample_id", "evidence_images"} <= sample.keys()
                or not all(isinstance(value, str) and value
                           for value in (run_id, sample["case_id"], sample["sample_id"]))):
            raise ValueError("sample reward rules require run, case and sample identities")
        _validate_aliases(sample.get("field_aliases", {}))
        images = sample["evidence_images"]
        if not isinstance(images, dict) or not images or any(
            type(step) is not int or not isinstance(digest, str)
            for step, digest in images.items()
        ):
            raise ValueError("sample reward rules require step-to-sha256 evidence_images")
        _validate_rule_references(sample, set(images))


def _validate_aliases(aliases):
    if not isinstance(aliases, dict) or any(
        not isinstance(name, str) or not name.strip()
        or not isinstance(canonical, str) or not canonical.strip()
        for name, canonical in aliases.items()
    ):
        raise ValueError("field aliases require nonempty source and canonical labels")


def _validate_rule_references(sample, image_steps):
    referenced = set()
    for key in ("approved_citations", "invalid_citations"):
        citations = sample.get(key, {})
        if not isinstance(citations, dict) or any(
            not isinstance(path, str) or not isinstance(steps, list)
            or any(type(step) is not int for step in steps)
            for path, steps in citations.items()
        ):
            raise ValueError(key + " requires paths with integer screenshot steps")
        referenced.update(step for steps in citations.values() for step in steps)
    for reading in sample.get("optional_fields", []):
        referenced.update(reading[key]["step"] for key in ("before", "after") if reading[key] is not None)
    values = sample.get("approved_values", {})
    if not isinstance(values, dict) or any(
        not isinstance(path, str) or not isinstance(alternatives, list)
        or any(not isinstance(value, str) for value in alternatives)
        for path, alternatives in values.items()
    ):
        raise ValueError("approved_values requires paths with literal string alternatives")
    if referenced - image_steps:
        raise ValueError("reward rule references a screenshot without an evidence hash")


def _source_run_id(row):
    direct = row.get("source_run_id")
    derived = row.get("derived_provenance", {}).get("source_run_id")
    if direct is not None and derived is not None and direct != derived:
        raise ValueError("review sample source_run_id differs from its provenance")
    source_run_id = direct if direct is not None else derived
    if source_run_id is None:
        raise ValueError("review sample requires source_run_id")
    return source_run_id


def _validate_binding(sample, row):
    if row["case_id"] != sample["case_id"] or row["id"] != sample["sample_id"]:
        raise ValueError("reward rules do not match the review case_id and sample_id")


def _verify_evidence_images(sample, row, dataset_root):
    steps = [frame["step"] for frame in row["review_frames"]]
    images = [dataset_root / item["image"] for message in row["messages"][:-1]
              for item in message["content"] if item["type"] == "image"]
    if len(images) != len(steps) or len(set(steps)) != len(steps):
        raise ValueError("review images must align with unique review_frames steps")
    if set(steps) != set(sample["evidence_images"]):
        raise ValueError("review screenshot steps differ from reward evidence_images")
    for step, path in zip(steps, images):
        if hashlib.sha256(path.read_bytes()).hexdigest() != sample["evidence_images"][step]:
            raise ValueError(f"reward evidence image changed at step {step}: {row['id']}")
