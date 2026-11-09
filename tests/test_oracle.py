from decimal import Decimal

import pytest

from guiqa.oracle import Evidence, judge, parse_usd


def test_no_save_cannot_confirm_bug():
    evidence = Evidence(preconditions=True, expected="Saved Pen Name", actual="Old")
    assert judge(evidence) == ("inconclusive", "trigger_not_verified")


def test_submitted_and_reopened_abnormal_value_confirms_bug():
    evidence = Evidence(preconditions=True, submitted=True, response_valid=True, reopened=True,
                        same_object=True, expected="Saved Pen Name", actual="Old")
    assert judge(evidence) == ("fail", "persisted_value_differs")


def test_fixed_build_false_positive_stays_oracle_pass():
    evidence = Evidence(preconditions=True, submitted=True, response_valid=True, reopened=True,
                        same_object=True, expected="Saved Pen Name", actual="Saved Pen Name")
    assert judge(evidence)[0] == "pass"


def test_browser_failure_is_infrastructure_error():
    assert judge(Evidence(healthy=False))[0] == "infra_error"


def test_submission_without_reopen_is_execution_failure():
    evidence = Evidence(preconditions=True, submitted=True, response_valid=True, expected="Saved Pen Name")
    assert judge(evidence) == ("inconclusive", "observation_not_completed")


def test_wrong_object_is_not_valid_observation():
    evidence = Evidence(preconditions=True, submitted=True, response_valid=True, reopened=True,
                        same_object=False, expected="Saved Pen Name", actual="Other")
    assert judge(evidence)[0] == "inconclusive"


def test_locale_amount_is_exact_and_keeps_full_magnitude():
    assert parse_usd("$1,234,567.89") == Decimal("1234567.89")
    assert parse_usd("$1,234.57") == Decimal("1234.57")
    with pytest.raises(ValueError):
        parse_usd("1.234.567,89")
