from guiqa.review_scenarios import content_key, scenario_metadata


def test_content_identity_is_independent_of_business_mapping_order():
    left = content_key("sweeper-142", "target", "Clear Description; preserve Hostname.",
                       {"description": "Printer service", "hostname": "printer.lab.local"}, {"description": ""})
    right = content_key("sweeper-142", "target", "Clear Description; preserve Hostname.",
                        {"hostname": "printer.lab.local", "description": "Printer service"}, {"description": ""})
    assert left == right
    assert left != content_key("sweeper-142", "target", "Clear Description; preserve Hostname.",
                               {"description": "Printer service", "hostname": "other.lab.local"}, {"description": ""})


def test_versions_and_stage_variants_bind_the_frozen_parent():
    args = ("sweeper-142", "target", "Clear Description.", {"description": "Printer"}, {"description": ""})
    record = scenario_metadata(*args, "core-train-php-001")
    assert record == {"scenario_fingerprint": content_key(*args), "content_key": content_key(*args),
                      "parent_scenario_id": "core-train-php-001"}
