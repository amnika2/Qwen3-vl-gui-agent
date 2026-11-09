from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from guiqa.apps.litecart_wolf import LitecartObserver, wolf_oracle, wolf_review
from guiqa.control import review_guard
from guiqa.oracle import Evidence, judge


def test_wrong_selected_country_is_not_labeled_as_a_product_bug():
    fixture = {'expected': 'saved country Albania', 'code': 'al2', 'name': 'Albania shipping',
               'description': 'Regional delivery', 'country_code': 'AL', 'city': ''}
    observer = LitecartObserver(SimpleNamespace(on=lambda *args: None), fixture)
    observer.evidence.preconditions = True
    observer._response(SimpleNamespace(url='http://guiqa.test/admin/?app=geo_zones&doc=edit_geo_zone', status=302,
        request=SimpleNamespace(method='POST', headers={'content-type': 'application/x-www-form-urlencoded'},
            post_data=urlencode({'save': 'Save', 'code': 'al2', 'name': 'Albania shipping',
                'description': 'Regional delivery', 'new_zone[country_code]': 'AX', 'new_zone[city]': ''}))))
    observer.evidence.reopened = observer.evidence.same_object = True
    observer.evidence.actual = 'saved country Aland Islands'
    assert judge(observer.evidence)[0] == 'inconclusive'


@pytest.mark.parametrize('kind,expected', [('edit', 'fail'), ('duplicate', 'pass')])
def test_duplicate_slug_error_is_scored_against_the_requested_operation(kind, expected):
    observer = SimpleNamespace(evidence=Evidence(preconditions=True, submitted=True, response_valid=True),
        fixture={'kind': kind}, error_seen={'text': 'Page with slug about-us already exists!', 'step': 6},
        success_seen=None)
    assert wolf_oracle(observer)[0] == expected
    context = SimpleNamespace(observer=observer, result={'actions': 6},
        history=[{'step': 3, 'action': {'action': 'type', 'text': 'About our team'}}])
    review = wolf_review(context)
    assert review['verdict'] == expected
    assert not review['submission_succeeded'] and not review['final_record_visible']
    assert review['submission_observed'] and review['business_rejection_visible']
    assert review['expected_rejection'] == (kind == 'duplicate')
    assert review_guard(review, profile='business_outcome_v1') is None


def test_wolf_success_toast_does_not_replace_persisted_value_evidence():
    evidence = Evidence(preconditions=True, submitted=True, response_valid=True, expected='new title')
    observer = SimpleNamespace(evidence=evidence, fixture={'kind': 'edit'}, error_seen=None,
                               success_seen={'text': 'Page has been saved!', 'step': 8})
    assert wolf_oracle(observer)[0] == 'inconclusive'
    evidence.reopened = evidence.same_object = True
    evidence.actual = 'old title'
    assert wolf_oracle(observer)[0] == 'fail'
    evidence.actual = 'new title'
    assert wolf_oracle(observer)[0] == 'pass'
