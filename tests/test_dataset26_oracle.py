from types import SimpleNamespace

from playwright.sync_api import sync_playwright

from guiqa.dataset26_oracle import Dataset26Observer, judge_business
from guiqa.oracle import Evidence


def business(case_id, fixture, readings, facts=None, network=None, url='http://app.test/'):
    return SimpleNamespace(case_id=case_id, fixture=fixture, facts=facts or {},
                           snapshots=[{'step': 9, 'url': url, 'readings': readings}],
                           requests=network or [], evidence=Evidence())


def test_next_instead_of_required_last_is_not_a_product_failure():
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            page.set_content('<div class="comments-container">PAGE1</div><span class="pagenav_next"><a href="#dcm=2">Next</a></span> <span class="pagenav_last"><a href="#dcm=4">Last</a></span>')
            fixture = {'control': 'pagenav_last', 'first_marker': 'PAGE1', 'expected': 'LASTPAGE'}
            observer = Dataset26Observer(page, 'sweeper-027', fixture)
            observer.capture(0, '000.png')
            target = page.locator('.pagenav_next a').bounding_box()
            observer.before_action({'action': 'click', 'coordinate': [(target['x'] + target['width'] / 2) / 1279 * 1000, (target['y'] + target['height'] / 2) / 899 * 1000]})
            observer.snapshots = [{'step': 2, 'url': 'http://app.test/#dcm=2', 'readings': {'comments': 'PAGE2'}}]
            assert judge_business(observer)[0] == 'inconclusive'
            target = page.locator('.pagenav_last a').bounding_box()
            observer.before_action({'action': 'click', 'coordinate': [(target['x'] + target['width'] / 2) / 1279 * 1000, (target['y'] + target['height'] / 2) / 899 * 1000]})
            assert judge_business(observer)[0] == 'fail'


def test_delete_request_for_other_object_is_not_a_product_failure():
    observer = business('sweeper-055', {'target': 'Delete me', 'retained': 'Keep me'},
                        {'reminders': 'Delete me Keep me'}, {'target_initial': 1, 'delete_target_id': '7'},
                        [{'path': '/index.php', 'step': 4, 'fields': {'reminder_id[]': '8'}}])
    assert judge_business(observer)[0] == 'inconclusive'


def test_file_search_without_upload_is_not_a_product_failure():
    observer = business('sweeper-049', {'filename': 'required.txt'}, {'file_present': False},
                        network=[{'path': '/search', 'query': '', 'method': 'POST', 'step': 4, 'fields': {}}])
    assert judge_business(observer)[0] == 'inconclusive'


def test_wrong_product_transfer_is_not_a_product_failure():
    observer = business('sweeper-074', {'note': 'KEEP_NOTE'}, {'stock_row': 'Cookies Pantry', 'location': 'Pantry'},
                        {'purchase_input': {'product_id': '1', 'location_id': '2'},
                         'transfer_input': {'product_id': '2', 'location_id_from': '2', 'location_id_to': '3'}},
                        [{'path': '/api/stock/products/1/add', 'step': 4, 'fields': {'amount': 2, 'note': 'KEEP_NOTE', 'location_id': '2'}},
                         {'path': '/api/stock/products/2/transfer', 'step': 7, 'fields': {'amount': 1, 'location_id_from': '2', 'location_id_to': '3'}}])
    assert judge_business(observer)[0] == 'inconclusive'


def test_no_results_text_containing_user_name_is_not_a_choice():
    observer = business('sweeper-146', {'user': 'Tilda 001'}, {},
                        {'existing_choices': {'value': 'Tilda 001', 'choice_visible': True},
                         'new_choices': {'value': 'No results matched Tilda 001', 'choice_visible': False}})
    assert judge_business(observer)[0] == 'fail'


def test_preservation_selects_actual_conditions_and_rejects_missing_kind():
    import pytest
    from guiqa.dataset26_execution import selected_index
    assert selected_index('sweeper-125', 'preservation', 0) == 4
    assert selected_index('sweeper-142', 'preservation', 0) == 3
    assert selected_index('sweeper-195', 'preservation', 0) == 4
    assert selected_index('sweeper-087', 'preservation', 0) == 8
    for case_id in ('sweeper-001', 'sweeper-083', 'sweeper-025'):
        with pytest.raises(ValueError, match='has no preservation condition'):
            selected_index(case_id, 'preservation', 0)


def test_upload_requires_the_target_control_and_the_supplied_file(tmp_path):
    import hashlib
    target = tmp_path / 'input.txt'
    target.write_text('expected bytes')
    wrong = tmp_path / 'wrong' / 'input.txt'
    wrong.parent.mkdir()
    wrong.write_text('other bytes')
    fixture = {'upload_filename': target.name, 'upload_sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            page.set_content('<input name="id_photo" type="file"><input id="other" type="file">')
            observer = Dataset26Observer(page, 'sweeper-001', fixture)
            observer.before_action({'action': 'upload', 'selector': '#other', 'file': str(target)})
            assert 'correct_upload' not in observer.facts
            observer.before_action({'action': 'upload', 'selector': 'input[name=id_photo]', 'file': str(wrong)})
            assert 'correct_upload' not in observer.facts
            observer.before_action({'action': 'upload', 'selector': 'input[name=id_photo]', 'file': str(target)})
            assert 'correct_upload' in observer.facts


def test_numeric_stock_amounts_with_trailing_decimal_are_valid():
    observer = business('sweeper-074', {'note': 'KEEP_NOTE'}, {'stock_row': 'Cookies Pantry KEEP_NOTE', 'location': 'Pantry'},
                        {'purchase_input': {'product_id': '1', 'location_id': '2', 'due_date': '2030-12-31'},
                         'transfer_input': {'product_id': '1', 'location_id_from': '2', 'location_id_to': '3'}},
                        [{'path': '/api/stock/products/1/add', 'step': 4, 'fields': {'amount': '2.', 'note': 'KEEP_NOTE', 'location_id': '2', 'best_before_date': '2030-12-31'}},
                         {'path': '/api/stock/products/1/transfer', 'step': 7, 'fields': {'amount': '1.', 'location_id_from': '2', 'location_id_to': '3'}}])
    assert judge_business(observer)[0] == 'pass'


def test_multipart_file_body_missing_from_cdp_is_not_parsed_as_text():
    observer = SimpleNamespace(started=True, case_id='sweeper-043', step=4, requests=[])
    request = SimpleNamespace(method='POST', headers={'content-type': 'multipart/form-data; boundary=upload'}, post_data_buffer=None)
    response = SimpleNamespace(request=request, url='http://app.test/p/notes/import', status=200)
    Dataset26Observer.response(observer, response)
    assert observer.requests[0]['fields'] == {}
    assert observer.requests[0]['path'] == '/p/notes/import'


def test_filegator_chunk_probe_proves_upload_attempt_before_post():
    observer = business('sweeper-049', {'filename': 'required.txt'}, {'file_present': False}, {'correct_upload': 1})
    observer.started, observer.step = True, 1
    request = SimpleNamespace(method='GET', headers={})
    response = SimpleNamespace(request=request, url='http://app.test/?r=/upload&resumableFilename=required.txt', status=200)
    Dataset26Observer.response(observer, response)
    assert judge_business(observer)[0] == 'fail'
