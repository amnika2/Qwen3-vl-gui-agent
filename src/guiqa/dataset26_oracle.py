"""Independent network and visible-page observations for dataset26 business tasks."""

from dataclasses import asdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .oracle import Evidence, in_view, request_fields


class Dataset26Observer:
    def __init__(self, page, case_id, fixture):
        self.page, self.case_id, self.fixture = page, case_id, fixture
        self.evidence = Evidence()
        self.step = 0
        self.started = False
        self.requests, self.snapshots, self.facts = [], [], {}
        self.legacy = legacy_observer(page, case_id, fixture)
        if self.legacy is not None:
            self.evidence = self.legacy.evidence
        page.on('response', self.response)

    def before_action(self, action):
        page, fixture, cid = self.page, self.fixture, self.case_id
        if cid == 'sweeper-027' and action['action'] == 'click':
            target = page.locator('span.' + fixture['control'] + ' a')
            if click_hits(page, action, target):
                self.facts['requested_pagination_clicked'] = self.step + 1
        elif cid == 'sweeper-055' and action['action'] == 'click':
            button = page.locator('#reminders button[type=submit]')
            if click_hits(page, action, button):
                selected = page.locator('#reminders input[name="reminder_id[]"]:checked')
                target = page.locator('#reminders tbody tr').filter(has_text=fixture['target']).locator('input[name="reminder_id[]"]')
                if selected.count() == 1 and target.count() == 1 and target.is_checked():
                    self.facts['delete_target_id'] = target.input_value()
        elif action['action'] == 'upload' and 'upload_sha256' in fixture:
            expected = {'sweeper-049': 'input[type="file"]', 'sweeper-001': 'input[name="id_photo"]',
                        'sweeper-043': '#importfileinput', 'sweeper-102': 'input[name="import_file"]'}[cid]
            chosen = page.locator(action['selector'])
            intended = page.locator(expected)
            correct_control = chosen.evaluate('(element, intended) => element === intended', intended.element_handle())
            path = Path(action['file'])
            if correct_control and path.name == fixture['upload_filename'] and hashlib.sha256(path.read_bytes()).hexdigest() == fixture['upload_sha256']:
                self.facts['correct_upload'] = self.step + 1
        elif cid == 'sweeper-074' and action['action'] == 'click':
            product = read(page.locator('#product_id_text_input'))
            amount = read(page.locator('#display_amount'))
            if click_hits(page, action, page.locator('#save-purchase-button')):
                location = read(page.locator('#location_id_text_input'))
                note = read(page.locator('#note'))
                due_date = read(page.locator('#best_before_date input.datetimepicker-input'))
                if product == 'Cookies' and amount is not None and Decimal(amount) == 2 and location == 'Candy cupboard' and note == fixture['note'] and due_date == fixture.get('due_date', '2999-12-31'):
                    self.facts['purchase_input'] = {'product_id': page.locator('#product_id').input_value(),
                                                    'location_id': page.locator('#location_id').input_value(),
                                                    'due_date': due_date, 'step': self.step + 1}
            if click_hits(page, action, page.locator('#save-transfer-button')):
                source = page.locator('#location_id_from option:checked').inner_text().strip()
                destination = page.locator('#location_id_to option:checked').inner_text().strip()
                if product == 'Cookies' and amount is not None and Decimal(amount) == 1 and source == 'Candy cupboard (Default location)' and destination == 'Pantry':
                    self.facts['transfer_input'] = {'product_id': page.locator('#product_id').input_value(),
                                                    'location_id_from': page.locator('#location_id_from').input_value(),
                                                    'location_id_to': page.locator('#location_id_to').input_value(), 'step': self.step + 1}

    def response(self, response):
        if not self.started:
            return
        request = response.request
        query = parse_qs(urlsplit(response.url).query)
        chunk_probe = self.case_id == 'sweeper-049' and request.method == 'GET' and query.get('r') == ['/upload']
        if request.method not in {'POST', 'PUT', 'PATCH', 'DELETE'} and not chunk_probe:
            return
        file_form = (self.case_id in {'sweeper-049', 'sweeper-001', 'sweeper-043', 'sweeper-102'}
                     and request.headers.get('content-type', '').startswith('multipart/form-data'))
        # Chromium omits file FormData bodies from CDP; before_action binds the actual file and input.
        fields = {key: values[-1] for key, values in query.items()} if chunk_probe else {} if file_form else request_fields(request)
        self.requests.append({'step': self.step + 1, 'path': urlsplit(response.url).path,
                              'query': urlsplit(response.url).query, 'method': request.method,
                              'fields': fields, 'status': response.status})

    def capture(self, step, screenshot):
        self.step = step
        self.started = True
        if self.legacy is not None:
            self.legacy.capture(step, screenshot)
            return
        page, fixture, cid = self.page, self.fixture, self.case_id
        body = page.locator('body').inner_text()
        path = urlsplit(page.url).path
        query = parse_qs(urlsplit(page.url).query)
        snapshot = {'step': step, 'screenshot': screenshot, 'url': page.url, 'readings': {}}
        readings = snapshot['readings']
        if cid == 'sweeper-004':
            if all(name in body for name in fixture['names']): self.facts['targets_seen'] = step
            field = page.locator('select[name="fields[status]"]')
            if visible(field): readings['status'] = field.locator('option:checked').inner_text().strip()
        elif cid == 'sweeper-146':
            dropdown = page.locator('.bootstrap-select > .dropdown-menu:visible')
            value = read(dropdown)
            if value is not None:
                name = 'existing_choices' if query.get('id') == ['1'] else 'new_choices'
                readings[name] = value
                choices = dropdown.get_by_role('option', name=fixture['user'], exact=True)
                self.facts[name] = {'value': value, 'choice_visible': any(visible(item) for item in choices.all()), 'step': step}
        elif cid == 'sweeper-055':
            panel = read(page.locator('#remind'))
            if panel is not None:
                readings['reminders'] = panel
                if fixture['target'] in panel: self.facts.setdefault('target_initial', step)
            if 'The selected reminder has been deleted.' in body: self.facts['confirmation'] = step
        elif cid == 'sweeper-049':
            results = page.locator('.modal-card-body li').filter(has_text=fixture['filename'])
            search = read(page.locator('input[placeholder="Name"]'))
            if search == fixture['filename']:
                readings['file_present'] = any(visible(item) for item in results.all())
                readings['search'] = search
        elif cid == 'sweeper-001':
            if query.get('rt') == ['account/edit']:
                readings['attachment_present'] = visible(page.get_by_role('link', name=fixture['filename'], exact=True))
        elif cid == 'sweeper-087':
            readings['email'] = read(page.locator('#email'))
            readings['dashboard'] = '/dashboard' in path and 'Dashboard' in body and 'Invoices' in body
            readings['rejection'] = 'Email or Password incorrect' in body and '/sessions/login' in path
        elif cid == 'sweeper-074':
            if path == '/purchase':
                readings['note_input'] = read(page.locator('#note'))
                readings['amount'] = read(page.locator('#display_amount'))
            if path == '/stockentries':
                row = page.locator('tr').filter(has_text='Cookies').filter(has_text='Pantry').first
                readings['stock_row'] = read(row)
                readings['location'] = page.locator('#location-filter option:checked').inner_text() if page.locator('#location-filter').count() else None
            toast = read(page.locator('.toast-success').last)
            if toast is not None: self.facts.setdefault('toasts', []).append({'value': toast, 'step': step})
        elif cid == 'sweeper-196':
            items = page.locator('#menuSettings div.editText[data-target="menuItemUpdate"]')
            if items.count() and visible(items.first):
                readings['order'] = [item.inner_text().strip() for item in items.all()[:2]]
                if readings['order'] == fixture['names']: self.facts.setdefault('initial_order', step)
        elif cid == 'sweeper-034':
            if path.endswith('/detail.php'):
                readings['media'] = query.get('media', [''])[0]
                readings['denied'] = 'Permission Denied' in body
                readings['detail_visible'] = visible(page.locator('.img_detail'))
                if readings['media'] == fixture['public'] + ':' + fixture['media']:
                    self.facts['allowed'] = {'value': readings['detail_visible'] and not readings['denied'], 'step': step}
        elif cid == 'sweeper-053':
            if fixture['ascii_body'] in body: self.facts['ordinary_article'] = step
            if fixture['body'] in body or 'Sorry, we could not find the page you requested' in body:
                readings['article_found'] = fixture['body'] in body
                readings['not_found'] = 'Sorry, we could not find the page you requested' in body
        elif cid == 'sweeper-151':
            plain = page.get_by_label(fixture['plain_field'], exact=True)
            unique = page.get_by_label(fixture['unique_field'], exact=True)
            if visible(plain) and visible(unique):
                readings['plain_value'], readings['unique_value'] = plain.input_value(), unique.input_value()
                readings['plain_error'] = plain.locator('xpath=..').inner_text()
                readings['unique_error'] = unique.locator('xpath=..').inner_text()
        elif cid == 'sweeper-102':
            if path == '/bookmarks' and query.get('q') == [fixture['token']]:
                readings['titles'] = [title for title in fixture['titles'] if title in body]
        elif cid == 'sweeper-083':
            table = read(page.locator('#bill_table'))
            if table is not None: readings['bills'] = table
            if 'Invalid Choice' in body or 'could not coerce' in body: self.facts['bill_error'] = step
            what = read(page.locator('input[name="what"]'))
            if what == fixture['what']: self.facts['bill_input'] = step
        elif cid == 'sweeper-020':
            readings['interval_error'] = 'At least one time interval' in body
            readings['updated'] = 'Updated watch' in body
            values = {name: read(page.locator(f'input[name="time_between_check-{name}"]')) for name in fixture['values']}
            if values == fixture['values']: self.facts['interval_input'] = step
        elif cid == 'sweeper-192':
            if path.endswith('/user/preferences'):
                email, height = read(page.locator('input[name="email"]')), read(page.locator('input[name="height"]'))
                if email is not None and height is not None:
                    readings.update(email=email, height=height)
                    self.facts.setdefault('baseline', {'email': email, 'height': height, 'step': step})
                    if email == fixture['email'] and height == fixture['height']: self.facts['requested_input'] = step
                    for name in ('first_name', 'last_name'): readings[name] = read(page.locator(f'input[name="{name}"]'))
                if 'already in use' in body or 'Please correct' in body: self.facts['validation_error'] = step
        elif cid == 'sweeper-045':
            selector = 'input[name="qty"]' if fixture['preservation'] else 'input[name="variant_qty"]'
            quantity = read(page.locator(selector))
            if quantity is not None:
                readings['quantity'] = quantity
                readings['product_identified'] = fixture['product'] in body
                sku = read(page.locator('input[name="variant_sku"]'))
                identified = fixture['product'] in body and (fixture['preservation'] or sku == fixture['sku'])
                self.facts.setdefault('opened_quantity', {'value': quantity, 'step': step, 'identified': identified})
        elif cid == 'sweeper-009':
            if fixture['title'] in body:
                readings['question'] = fixture['title']
                readings['accepted'] = visible(page.get_by_text('Accepted', exact=True))
                readings['answer_error'] = 'Answer do not found' in body
        elif cid == 'sweeper-027':
            comments = read(page.locator('.comments-container'))
            if comments is not None:
                readings['comments'] = comments
                if fixture['first_marker'] in comments: self.facts.setdefault('first_page', step)
        elif cid == 'sweeper-043':
            if page.locator('iframe[name="ace_outer"]').count():
                editor = page.frame_locator('iframe[name="ace_outer"]').frame_locator('iframe[name="ace_inner"]').locator('body')
                readings['editor'] = editor.inner_text()
                readings['pad'] = path.rsplit('/', 1)[-1]
                readings['import_error'] = page.locator('#importmessagefail:visible').count() > 0
        elif cid == 'sweeper-101':
            warning = page.locator('.link-exists')
            if visible(page.locator('input[name="url"]')):
                readings['url'] = read(page.locator('input[name="url"]'))
                readings['restore'] = visible(warning.locator('.link-exists-restore'))
                readings['edit'] = visible(warning.get_by_role('link', name='Edit Link', exact=True))
        elif cid == 'sweeper-025':
            title = read(page.locator('cp-fieldcontainer:has(label[title="title"]) input[type="text"]'))
            if title is not None:
                readings['title'] = title
                if title == fixture['marker']: self.facts.setdefault('initial_title', step)
            if 'Singleton removed' in body: self.facts['deleted'] = step
        elif cid == 'sweeper-037':
            table = read(page.locator('#form-entries'))
            if table is not None:
                readings['entries'] = table
                readings['filter'] = page.locator(f'select[name="{fixture["filter"]}"]').input_value()
        self.snapshots.append(snapshot)
        self.evidence.events.append(snapshot)

    def data(self):
        return {**asdict(self.evidence), 'case_id': self.case_id, 'network': self.requests,
                'facts': self.facts, 'snapshots': self.snapshots}


def visible(locator):
    return locator.count() > 0 and locator.first.is_visible() and in_view(locator.first)


def click_hits(page, action, locator):
    if not visible(locator):
        return False
    box = locator.first.bounding_box()
    point = action['coordinate']
    x, y = point[0] * (page.viewport_size['width'] - 1) / 1000, point[1] * (page.viewport_size['height'] - 1) / 1000
    return box['x'] <= x <= box['x'] + box['width'] and box['y'] <= y <= box['y'] + box['height']


def read(locator):
    if not visible(locator):
        return None
    element = locator.first
    return element.input_value() if element.evaluate('element => /^(INPUT|SELECT|TEXTAREA)$/.test(element.tagName)') else element.inner_text()


def legacy_observer(page, case_id, fixture):
    if case_id == 'sweeper-005':
        from .oracle import ItemObserver
        return ItemObserver(page, fixture)
    if case_id == 'sweeper-125':
        from .dataset26_existing import NumericWeightObserver
        return NumericWeightObserver(page, fixture)
    if case_id == 'sweeper-142':
        from .apps.mantis_phpipam import PhpipamObserver
        return PhpipamObserver(page, fixture)
    if case_id == 'sweeper-195':
        from .apps.litecart_wolf import WolfObserver
        return WolfObserver(page, fixture)
    return None


def judge(observer):
    if not observer.evidence.healthy:
        return 'infra_error', 'environment_failure'
    if observer.legacy is not None:
        if observer.case_id == 'sweeper-195':
            from .apps.litecart_wolf import wolf_oracle
            return wolf_oracle(observer.legacy)
        from .oracle import judge as persistence_judge
        return persistence_judge(observer.evidence)
    return judge_business(observer)


def judge_business(observer):
    cid, fixture, facts = observer.case_id, observer.fixture, observer.facts
    snapshots, network = observer.snapshots, observer.requests
    latest = snapshots[-1]['readings'] if snapshots else {}
    post = lambda fragment: [event for event in network if fragment in event['path']]
    triggered, observed, matches = False, False, False
    checks = {}
    if cid == 'sweeper-004':
        triggered = 'targets_seen' in facts
        observed = 'status' in latest
        matches = latest.get('status', '').casefold() == 'original'
        checks = {'default_status': latest.get('status')}
    elif cid == 'sweeper-146':
        triggered = 'existing_choices' in facts
        observed = 'new_choices' in facts
        matches = all(facts[name]['choice_visible'] for name in ('existing_choices', 'new_choices')) if triggered and observed else False
        checks = {name: facts.get(name) for name in ('existing_choices', 'new_choices')}
    elif cid == 'sweeper-055':
        triggered = 'target_initial' in facts and 'delete_target_id' in facts and any(
            event['fields'].get('reminder_id[]') == facts['delete_target_id'] for event in post('index.php'))
        observed = 'reminders' in latest and bool(network) and snapshots[-1]['step'] > network[-1]['step']
        text = latest.get('reminders', '')
        matches = 'confirmation' in facts and fixture['target'] not in text and fixture['retained'] in text
        checks = {'target_absent': fixture['target'] not in text, 'retained_present': fixture['retained'] in text}
    elif cid in {'sweeper-049', 'sweeper-001'}:
        if cid == 'sweeper-049':
            uploads = [event for event in network if parse_qs(event['query']).get('r') == ['/upload']
                       and parse_qs(event['query']).get('resumableFilename') == [fixture['filename']]]
        else:
            uploads = [event for event in network if event['path'] == '/index.php'
                       and parse_qs(event['query']).get('rt') == ['account/edit'] and event['method'] == 'POST']
        triggered = 'correct_upload' in facts and any(event['step'] >= facts['correct_upload'] for event in uploads)
        key = 'file_present' if cid == 'sweeper-049' else 'attachment_present'
        observed = key in latest
        matches = latest.get(key) is True
        checks = {key: latest.get(key), 'filename': fixture['filename']}
    elif cid == 'sweeper-087':
        login = [event for event in post('/sessions/login') if event['fields'].get('email') == fixture['email'] and event['fields'].get('password') == fixture['password']]
        triggered = bool(login)
        observed = latest.get('dashboard') is True or latest.get('rejection') is True
        denied = False
        if triggered and latest.get('rejection'):
            origin = urlsplit(observer.page.url)
            response = observer.page.context.request.get(origin.scheme + '://' + origin.netloc + '/dashboard')
            denied = '/sessions/login' in response.url
        matches = latest.get('rejection') is True and denied and not latest.get('dashboard') if fixture['inactive'] else latest.get('dashboard') is True
        checks = {'dashboard': latest.get('dashboard'), 'rejection': latest.get('rejection'), 'protected_page_denied': denied}
    elif cid == 'sweeper-074':
        purchase_input, transfer_input = facts.get('purchase_input', {}), facts.get('transfer_input', {})
        purchase = [event for event in post('/add') if event['fields'].get('note') == fixture['note'] and Decimal(str(event['fields']['amount'])) == 2
                    and event['path'].endswith('/products/' + purchase_input.get('product_id', '') + '/add')
                    and str(event['fields'].get('location_id')) == purchase_input.get('location_id')
                    and event['fields'].get('best_before_date') == purchase_input.get('due_date')]
        transfer = [event for event in post('/transfer') if Decimal(str(event['fields']['amount'])) == 1
                    and event['path'].endswith('/products/' + purchase_input.get('product_id', '') + '/transfer')
                    and transfer_input.get('product_id') == purchase_input.get('product_id')
                    and all(str(event['fields'].get(key)) == transfer_input.get(key) for key in ('location_id_from', 'location_id_to'))]
        triggered = bool(purchase and transfer) and purchase[0]['step'] < transfer[-1]['step']
        observed = latest.get('stock_row') is not None and latest.get('location', '').strip() == 'Pantry'
        matches = fixture['note'] in latest.get('stock_row', '')
        checks = {'pantry_stock_row': latest.get('stock_row'), 'purchase_events': len(purchase), 'transfer_events': len(transfer)}
    elif cid == 'sweeper-196':
        triggered = 'initial_order' in facts and bool(network)
        observed = 'order' in latest
        matches = latest.get('order') == list(reversed(fixture['names']))
        checks = {'menu_order': latest.get('order')}
    elif cid == 'sweeper-034':
        triggered = 'allowed' in facts
        observed = latest.get('media') == fixture['secret'] + ':' + fixture['media']
        matches = facts.get('allowed', {}).get('value') and latest.get('denied') and not latest.get('detail_visible')
        checks = latest
    elif cid == 'sweeper-053':
        triggered = 'ordinary_article' in facts
        observed = 'article_found' in latest
        matches = latest.get('article_found') and not latest.get('not_found')
        checks = latest
    elif cid == 'sweeper-151':
        attempts = [event for event in network if '/records' in event['path'] and event['fields'].get(fixture['unique_field']) == fixture['token']]
        triggered = len(attempts) >= 2 and latest.get('plain_value') == fixture['second']
        observed = 'unique_error' in latest and 'Value must be unique' in (latest.get('unique_error', '') + latest.get('plain_error', ''))
        matches = 'Value must be unique' in latest.get('unique_error', '') and 'Value must be unique' not in latest.get('plain_error', '')
        checks = latest
    elif cid == 'sweeper-102':
        triggered = 'correct_upload' in facts and bool(post('/settings/import'))
        observed = 'titles' in latest
        matches = len(latest.get('titles', [])) == (2 if fixture['preservation'] else 1)
        checks = latest
    elif cid == 'sweeper-083':
        triggered = 'bill_input' in facts and bool(network)
        observed = 'bills' in latest and (fixture['what'] in latest['bills'] or 'bill_error' in facts)
        matches = fixture['what'] in latest.get('bills', '') and 'bill_error' not in facts
        checks = {'bill_present': fixture['what'] in latest.get('bills', ''), 'bill_error': 'bill_error' in facts}
    elif cid == 'sweeper-020':
        triggered = 'interval_input' in facts and bool(post('/edit/'))
        observed = latest.get('interval_error') or latest.get('updated')
        matches = latest.get('updated') and not latest.get('interval_error') if fixture['preservation'] else latest.get('interval_error') and not latest.get('updated')
        checks = latest
    elif cid == 'sweeper-192':
        submits = [event for event in post('/user/preferences') if event['fields'].get('email') == fixture['email'] and event['fields'].get('height') == fixture['height']]
        triggered = bool(submits) and 'baseline' in facts and 'requested_input' in facts
        observed = 'email' in latest and snapshots[-1]['step'] > (submits[-1]['step'] if submits else 0)
        expected_email = fixture['email'] if fixture['preservation'] else facts.get('baseline', {}).get('email')
        expected_height = fixture['height'] if fixture['preservation'] else facts.get('baseline', {}).get('height')
        matches = latest.get('email') == expected_email and latest.get('height') == expected_height and ('validation_error' in facts) != fixture['preservation']
        if fixture['preservation']:
            matches = matches and all(latest.get(name) == fixture[name] for name in ('first_name', 'last_name'))
        checks = {'email': latest.get('email'), 'height': latest.get('height'), 'error_seen': 'validation_error' in facts}
    elif cid == 'sweeper-045':
        opened = facts.get('opened_quantity', {})
        triggered = bool(opened) and opened.get('identified')
        observed = bool(opened)
        matches = opened.get('value') == fixture['expected']
        checks = {'opened_quantity': opened.get('value'), 'expected': fixture['expected']}
    elif cid == 'sweeper-009':
        triggered = bool(post('/answer')) and latest.get('question') == fixture['title']
        observed = latest.get('accepted') or latest.get('answer_error')
        matches = latest.get('accepted') and not latest.get('answer_error')
        checks = latest
    elif cid == 'sweeper-027':
        triggered = 'first_page' in facts and 'requested_pagination_clicked' in facts and 'dcm=' in urlsplit(snapshots[-1]['url']).fragment
        observed = 'comments' in latest and triggered
        matches = fixture['expected'] in latest.get('comments', '')
        checks = {'expected_marker': fixture['expected'], 'comments': latest.get('comments')}
    elif cid == 'sweeper-043':
        triggered = 'correct_upload' in facts and bool(post('/import')) and latest.get('pad') == fixture['pad']
        observed = 'editor' in latest and (latest.get('import_error') or fixture['text'].strip() in latest.get('editor', ''))
        matches = fixture['text'].strip() in latest.get('editor', '') and 'Welcome to Etherpad' not in latest.get('editor', '') and not latest.get('import_error')
        checks = latest
    elif cid == 'sweeper-101':
        triggered = latest.get('url') == fixture['url'] and any(event['fields'].get('query') == fixture['url'] for event in post('/fetch/existing-links'))
        observed = 'restore' in latest
        matches = latest.get('edit') and not latest.get('restore') if fixture['preservation'] else latest.get('restore')
        checks = latest
    elif cid == 'sweeper-025':
        triggered = 'initial_title' in facts and 'deleted' in facts and len(network) >= 2
        observed = 'title' in latest
        matches = latest.get('title') == ''
        checks = latest
    elif cid == 'sweeper-037':
        triggered = latest.get('filter') == fixture['value']
        observed = 'entries' in latest
        matches = fixture['expected'] in latest.get('entries', '') and all(title not in latest.get('entries', '') for title in fixture['excluded'])
        checks = latest
    observer.evidence.preconditions = bool(triggered)
    observer.evidence.actual = json.dumps(checks, ensure_ascii=False)
    if not triggered:
        return 'inconclusive', 'business_trigger_not_verified'
    if not observed:
        return 'inconclusive', 'business_result_not_observed'
    return ('pass', 'business_requirement_matches') if matches else ('fail', 'business_requirement_violated')
