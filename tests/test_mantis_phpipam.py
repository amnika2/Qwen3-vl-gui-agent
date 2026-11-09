from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from playwright.sync_api import sync_playwright

from guiqa.apps.mantis_phpipam import MantisObserver,PhpipamObserver,mantis_oracle,mantis_review
from guiqa.oracle import judge


def response(path,fields,status=200):
    return SimpleNamespace(url='http://guiqa.test'+path,status=status,
        request=SimpleNamespace(method='POST',headers={'content-type':'application/x-www-form-urlencoded'},
            post_data=urlencode(fields)),text=lambda:'<div class="alert alert-success">IP edit successful</div>')


@pytest.mark.parametrize('submitted,verdict',[(True,'fail'),(False,'inconclusive')])
def test_mantis_business_rejection_requires_valid_submission(submitted,verdict):
    with sync_playwright() as pw:
        with pw.chromium.launch() as browser:
            page=browser.new_page()
            page.set_content('<h1>APPLICATION ERROR #29</h1><p>Invalid value for "version"</p>')
            fixture={'summary':'Report A','description':'Line one\nLine two','version':'1.1.0','project_id':1}
            observer=MantisObserver(page,fixture);observer.evidence.preconditions=True
            observer._response(response('/bug_report.php',{'summary':'Report A' if submitted else 'wrong report',
                'description':'Line one\r\nLine two','product_version':'1.1.0','category_id':'1','project_id':'1'},400))
            observer.capture(3,'error.png')
            assert mantis_oracle(observer)[0]==verdict
            review=mantis_review(SimpleNamespace(observer=observer,result={'actions':3},history=[]))
            assert not review['submission_succeeded'] and not review['final_record_visible']
            assert review['verdict']==('fail' if submitted else 'blocked')


@pytest.mark.parametrize('reopened_id,actual_description,verdict',[
    ('1','Server1','fail'),('1','','pass'),('2','','inconclusive')])
def test_phpipam_reopen_checks_whole_state_and_same_address(reopened_id,actual_description,verdict):
    with sync_playwright() as pw:
        with pw.chromium.launch() as browser:
            page=browser.new_page()
            page.set_content('<input name="addressId" value="1"><input name="description" value="Server1">'
                             '<input name="hostname" value="server1.test">')
            fixture={'object_id':1,'description':'Server1','hostname':'server1.test','changes':{'description':''}}
            observer=PhpipamObserver(page,fixture);observer.capture(1,'initial.png')
            page.locator('[name=description]').fill('')
            observer.capture(2,'entered.png')
            assert observer.evidence.preconditions
            observer._response(response('/app/subnets/addresses/address-modify-submit.php',
                {'action':'edit','id':'1','description':'','hostname':'server1.test'}))
            observer._response(response('/app/subnets/addresses/address-modify.php',{'action':'edit','id':reopened_id}))
            page.locator('[name=addressId]').fill(reopened_id)
            page.locator('[name=description]').fill(actual_description)
            observer.capture(4,'reopened.png')
            assert judge(observer.evidence)[0]==verdict
