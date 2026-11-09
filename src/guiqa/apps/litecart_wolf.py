"""Pinned LiteCart and Wolf CMS application instances and observed SFT flows."""
import hashlib
import json
from dataclasses import asdict
from urllib.parse import parse_qs, urlparse

from ..native import NativeInstance
from ..browser import settle
from ..oracle import Evidence, in_view, request_fields
from ..sft_flows import SftFlow, click_at, click_locator, press, scroll_to, type_text
from ..sft_data import observed_review, replay_flow


class LitecartInstance(NativeInstance):
    case_id = 'sweeper-105'
    slug = 'litecart'
    web_root = 'public_html'
    health_path = '/install/'

    def configure(self):
        # Keep the pinned checkout intact: the installer otherwise downloads replacement files.
        ini = self.socket_dir / 'php-ini'
        ini.mkdir()
        offline = self.socket_dir / 'offline-network.php'
        offline.write_text("<?php function stream_socket_client($address, &$errno=null, &$errstr=null, $timeout=null, $flags=0, $context=null) { $errno=111; $errstr='Offline benchmark instance'; return false; }\n")
        (ini / 'offline.ini').write_text(f'disable_functions=stream_socket_client\nauto_prepend_file={offline}\n')
        self.env['PHP_INI_SCAN_DIR'] = str(ini)

    def _seed(self):
        response = self.http.post(self.url + '/install/install.php', data={
            'install': '1', 'db_server': 'localhost', 'db_username': 'root',
            'db_password': '', 'db_database': 'guiqa', 'db_table_prefix': 'lc_',
            'db_collation': 'utf8mb4_unicode_ci', 'timezone': 'UTC', 'admin_folder': 'admin',
            'username': 'tessa.quinn', 'password': 'bramble8oak',
        }, timeout=60)
        (self.directory / 'install-response.html').write_text(response.text)
        response.raise_for_status()
        if not (self.app / 'public_html/includes/config.inc.php').exists():
            raise RuntimeError('LiteCart installer did not create includes/config.inc.php')
        self.sql("USE guiqa; UPDATE lc_settings SET value='0' WHERE `key`='store_online';")
        verify_business_source(self, ['public_html/admin/geo_zones.app/edit_geo_zone.inc.php', 'public_html/includes/entities/ent_geo_zone.inc.php'])

    def seed_training_page(self, fixture):
        if fixture.get('initial'):
            values = ','.join(sql_text(fixture['initial'][key]) for key in ('code', 'name', 'description'))
            self.sql("USE guiqa; INSERT INTO lc_geo_zones (id,code,name,description,date_created,date_updated) VALUES (1,"
                     + values + ",NOW(),NOW());")


class WolfInstance(NativeInstance):
    case_id = 'sweeper-195'
    slug = 'wolfcms'
    web_root = '.'
    health_path = '/wolf/install/'
    php_name = 'php74/php'
    runtime_description = 'PHP 7.4.3 / MariaDB 10.11.14, user-owned processes'

    def configure(self):
        (self.app / 'config.php').touch()

    def _seed(self):
        response = self.http.post(self.url + '/wolf/install/index.php', data={
            'install': '1', 'commit': '1', 'config[db_driver]': 'mysql',
            'config[db_host]': 'localhost', 'config[db_port]': '3306',
            'config[db_socket]': str(self.socket), 'config[db_user]': 'root',
            'config[db_pass]': '', 'config[db_name]': 'guiqa', 'config[table_prefix]': 'wolf_',
            'config[admin_username]': 'niles.wade', 'config[url_suffix]': '',
        }, timeout=30)
        (self.directory / 'install-response.html').write_text(response.text)
        response.raise_for_status()
        salt = 'guiqawolfsalt'
        hashed = hashlib.sha512(('corkLatch5!' + salt).encode()).hexdigest()
        self.sql(f"USE guiqa; UPDATE wolf_user SET username='niles.wade', password='{hashed}', salt='{salt}' WHERE id=1;")
        # The bundled Kses uses PHP 5's removed /e regex modifier. The native setting
        # keeps plain-text title edits on the supported path, equally for both builds.
        self.sql("USE guiqa; UPDATE wolf_setting SET value='on' WHERE name='allow_html_title';")
        config = self.app / 'config.php'
        config.write_text(config.read_text().replace("define('CHECK_UPDATES', true)", "define('CHECK_UPDATES', false)"))
        config.chmod(0o444)
        verify_business_source(self, ['wolf/app/controllers/PageController.php'])

    def seed_training_page(self, fixture):
        self.sql("USE guiqa; UPDATE wolf_page SET title=" + sql_text(fixture['initial_title'])
                 + ",description='',keywords='',breadcrumb='' WHERE id=3;")


def litecart_login(page, url, fixture):
    page.goto(url + '/admin/login.php', wait_until='domcontentloaded')
    page.locator('input[name=username]').fill(fixture['username'])
    page.locator('input[name=password]').fill(fixture['password'])
    page.locator('button[name=login]').click()
    page.wait_for_url(lambda value: 'login.php' not in str(value), wait_until='domcontentloaded')
    route = '/admin/?app=geo_zones&doc=geo_zones'
    if fixture.get('start') == 'editor':
        route = '/admin/?app=geo_zones&doc=edit_geo_zone' + ('&geo_zone_id=1' if fixture.get('initial') else '')
    page.goto(url + route, wait_until='domcontentloaded')
    page.locator('input[name=code]' if fixture.get('start') == 'editor' else 'a[href*="doc=edit_geo_zone"]').first.wait_for()
    settle(page)


def wolf_login(page, url, fixture):
    page.goto(url + '/?/admin/login', wait_until='domcontentloaded')
    page.locator('#login-username').fill(fixture['username'])
    page.locator('#login-password').fill(fixture['password'])
    page.locator('input[type=submit]').click()
    page.wait_for_url(lambda value: 'login' not in str(value), wait_until='domcontentloaded')
    route = '/?/admin/page'
    if fixture.get('start') == 'editor':
        route += '/edit/3' if fixture['kind'] == 'edit' else '/add/1'
    page.goto(url + route, wait_until='domcontentloaded')
    page.locator('#page_title' if fixture.get('start') == 'editor' else 'a[href$="page/edit/3"]').first.wait_for()
    settle(page)


def sql_text(value):
    return "'" + value.replace('\\', '\\\\').replace("'", "''") + "'"


def verify_business_source(instance, paths):
    checkout = instance.root / 'vendor/apps' / instance.slug / ('base' if instance.build == 'A' else 'fixed')
    hashes = {}
    for path in paths:
        actual = hashlib.sha256((instance.app / path).read_bytes()).hexdigest()
        expected = hashlib.sha256((checkout / path).read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f'Installer changed pinned business file: {path}')
        hashes[path] = actual
    (instance.directory / 'business-source.json').write_text(json.dumps(hashes, indent=2) + '\n')


class LitecartObserver:
    def __init__(self, page, fixture):
        self.page, self.fixture = page, fixture
        self.evidence = Evidence(expected=fixture['expected'])
        self.step = 0
        self.submitted_at = None
        self.reopened_at = None
        page.on('response', self._response)

    def capture(self, step, screenshot):
        self.step = step
        query = parse_qs(urlparse(self.page.url).query)
        if query.get('app') != ['geo_zones'] or query.get('doc') != ['edit_geo_zone']:
            return
        fields = {field: self.page.locator(f'input[name="{field}"]').input_value()
                  for field in ('code', 'name', 'description')}
        if self.submitted_at is None:
            initial = self.fixture.get('initial', {'code': '', 'name': '', 'description': ''})
            if fields == initial:
                self.evidence.preconditions = True
            return
        if self.reopened_at is None or self.reopened_at <= self.submitted_at or self.evidence.reopened:
            return
        table = self.page.locator('table.data-table')
        if not in_view(table):
            return
        countries = [text.strip() for text in table.locator('tbody tr td:nth-child(2)').all_inner_texts()]
        cities = [text.strip() for text in table.locator('tbody tr td:nth-child(4)').all_inner_texts()]
        actual = {**fields, 'countries': countries, 'cities': cities}
        self.evidence.reopened = True
        self.evidence.same_object = query.get('geo_zone_id') == ['1']
        self.evidence.actual = json.dumps(actual, ensure_ascii=False, sort_keys=True)
        self.evidence.events.append({'kind': 'reopened_geo_zone', 'step': step, 'object_id': query.get('geo_zone_id'),
            'actual': actual, 'screenshot': screenshot})

    def _response(self, response):
        request = response.request
        query = parse_qs(urlparse(response.url).query)
        if query.get('app') != ['geo_zones']:
            return
        import time
        now = time.monotonic()
        if request.method == 'GET' and request.is_navigation_request() and self.submitted_at is not None:
            self.reopened_at = now
        if request.method != 'POST' or query.get('doc') != ['edit_geo_zone']:
            return
        fields = request_fields(request)
        if 'save' not in fields:
            return
        correct = all(fields.get(key) == self.fixture[key] for key in ('code', 'name', 'description'))
        correct = correct and fields.get('new_zone[country_code]', '') == self.fixture.get('country_code', '')
        correct = correct and fields.get('new_zone[city]', '') == self.fixture.get('city', '')
        self.evidence.submitted = correct
        self.evidence.response_valid = response.status == 302
        self.submitted_at, self.reopened_at = now, None
        self.evidence.events.append({'kind': 'submit_response', 'step': self.step + 1, 'status': response.status,
            'accepted': response.status == 302, 'correct_input': correct,
            'submitted_values': {k: fields.get(k) for k in ('code', 'name', 'description', 'new_zone[country_code]', 'new_zone[city]')}})

    def data(self):
        return asdict(self.evidence)


class WolfObserver:
    def __init__(self, page, fixture):
        self.page, self.fixture = page, fixture
        self.evidence = Evidence(expected=fixture['expected'])
        self.step = 0
        self.submitted = False
        self.error_seen = None
        self.success_seen = None
        self.reloaded = False
        self.visible_values = {}
        page.on('response', self._response)

    def capture(self, step, screenshot):
        self.step = step
        route = urlparse(self.page.url).query
        expected_route = '/admin/page/edit/3' if self.fixture['kind'] == 'edit' else '/admin/page/add'
        if not route.startswith(expected_route):
            return
        if not self.submitted:
            title = self.page.locator('#page_title').input_value()
            parent = self.page.locator('#page_parent_id').input_value()
            slug = self.page.locator('#page_slug').input_value()
            if self.fixture['kind'] == 'edit':
                matched = title == self.fixture['initial_title'] and slug == 'about-us' and parent == '1'
            else:
                matched = title == '' and parent == '1'
            if matched:
                self.evidence.preconditions = True
            return
        messages = self.page.locator('#error, #success').evaluate_all('''elements => elements.filter(element => {
            const box = element.getBoundingClientRect();
            const x = box.left + box.width / 2, y = box.top + box.height / 2;
            return box.width > 0 && box.height > 0 && getComputedStyle(element).visibility === 'visible'
                && x >= 0 && x < innerWidth && y >= 0 && y < innerHeight;
        }).map(element => ({id: element.id, text: element.innerText.trim()}))''')
        for message in messages:
            text = message['text']
            if (message['id'] == 'error' and 'Page with slug' in text
                    and 'about-us' in text and 'already exists!' in text):
                self.error_seen = {'kind': 'duplicate_slug_error', 'step': step, 'text': text, 'screenshot': screenshot}
                self.evidence.events.append(self.error_seen)
            if message['id'] == 'success' and 'Page has been saved!' in text:
                self.success_seen = {'kind': 'save_success', 'step': step, 'text': text, 'screenshot': screenshot}
                self.evidence.events.append(self.success_seen)
        if self.fixture['kind'] == 'edit' and self.reloaded:
            for field in self.fixture['fields']:
                locator = self.page.locator('#page_' + field)
                if in_view(locator):
                    self.visible_values[field] = locator.input_value()
                    self.evidence.events.append({'kind': 'reopened_field', 'step': step, 'field': field,
                        'value': self.visible_values[field], 'screenshot': screenshot})
            if self.visible_values.keys() == self.fixture['fields'].keys():
                self.evidence.reopened = self.evidence.same_object = True
                self.evidence.actual = json.dumps(self.visible_values, ensure_ascii=False, sort_keys=True)

    def _response(self, response):
        request = response.request
        route = urlparse(response.url).query
        if self.submitted and request.method == 'GET' and request.is_navigation_request() and route.startswith('/admin/page/edit/3'):
            self.reloaded = True
        if request.method != 'POST' or not route.startswith('/admin/page/'):
            return
        fields = request_fields(request)
        if 'continue' not in fields:
            return
        correct = fields.get('page[slug]') == 'about-us' and fields.get('page[parent_id]') == '1'
        correct = correct and all(fields.get('page[' + key + ']') == value for key, value in self.fixture['fields'].items())
        self.submitted = True
        self.evidence.submitted = correct
        self.evidence.response_valid = response.status in (200, 302)
        self.evidence.events.append({'kind': 'submit_response', 'step': self.step + 1,
            'object_id': '3' if self.fixture['kind'] == 'edit' else 'new-child-of-1',
            'status': response.status, 'accepted': response.status == 302,
            'correct_input': correct, 'slug': fields.get('page[slug]'),
            'submitted_values': {key: fields.get('page[' + key + ']') for key in self.fixture['fields']}})

    def data(self):
        return {**asdict(self.evidence), 'error_seen': self.error_seen, 'success_seen': self.success_seen}


def wolf_oracle(observer):
    evidence = observer.evidence
    if not evidence.healthy:
        return 'infra_error', 'environment_failure'
    if not (evidence.preconditions and evidence.submitted and evidence.response_valid):
        return 'inconclusive', 'trigger_not_verified'
    if observer.error_seen:
        return ('fail', 'existing_page_self_collision') if observer.fixture['kind'] == 'edit' else ('pass', 'true_duplicate_slug_rejected')
    if observer.fixture['kind'] == 'duplicate' and observer.success_seen:
        return 'fail', 'duplicate_slug_was_accepted'
    if observer.fixture['kind'] == 'edit' and observer.success_seen and evidence.reopened and evidence.same_object:
        return ('pass', 'saved_fields_match') if evidence.actual == evidence.expected else ('fail', 'saved_fields_differ')
    return 'inconclusive', 'outcome_not_observed'


def wolf_review(context):
    observer = context.observer
    verdict, reason = wolf_oracle(observer)
    event = observer.error_seen or observer.success_seen
    input_steps = [row['step'] for row in context.history if row['action'] and row['action']['action'] == 'type']
    observation = f"{reason}; " + (event['text'] if event else 'no completed business outcome visible')
    if observer.evidence.actual is not None:
        observation += '; reopened values: ' + observer.evidence.actual
    return {'action': 'finish', 'verdict': verdict if verdict in {'pass', 'fail'} else 'blocked',
            'requested_value_entered': observer.evidence.submitted,
            'submission_succeeded': observer.success_seen is not None,
            'final_record_visible': observer.evidence.reopened and observer.evidence.same_object,
            'submission_observed': bool(observer.evidence.submitted and observer.evidence.response_valid and event),
            'business_rejection_visible': observer.error_seen is not None,
            'expected_rejection': observer.fixture['kind'] == 'duplicate',
            'observation': observation,
            'evidence_steps': sorted(set(input_steps + [context.result['actions']] + ([event['step']] if event else [])))}


def litecart_flow(fixture, build):
    steps, navigation = [], []
    if fixture['start'] == 'list':
        selector = 'a[href*="doc=edit_geo_zone"]' if not fixture.get('initial') else 'a[href*="geo_zone_id=1"]'
        steps.append(click_locator(lambda page: page.locator(selector).first));navigation.append(len(steps))
    for field in fixture['field_order']:
        steps.extend([click_at(f'input[name="{field}"]'), press('Control+a'), type_text(fixture[field])])
    if fixture.get('country_code'):
        steps.extend([click_at('select[name="new_zone[country_code]"]'), press('Home')])
        steps.extend(press('ArrowDown') for _ in range(fixture['country_index']))
        steps.append(press('Enter'))
    if fixture.get('city'):
        steps.extend([click_at('input[name="new_zone[city]"]'), type_text(fixture['city'])])
    input_step = len(steps)
    steps.append(click_at('button[name=save]'));submit_step = len(steps);navigation.append(submit_step)
    steps.append(click_locator(lambda page: page.locator('a[href*="doc=edit_geo_zone"]').filter(has_text=fixture['name']).first))
    navigation.append(len(steps))
    return SftFlow(steps, input_step, submit_step, (0, input_step, submit_step, len(steps)), tuple(navigation))


def wolf_flow(fixture, build):
    steps, navigation, input_steps = [], [], []
    if fixture['start'] == 'list':
        path = 'page/edit/3' if fixture['kind'] == 'edit' else 'page/add/1'
        steps.append(click_locator(lambda page: page.locator(f'a[href$="{path}"]').first));navigation.append(len(steps))
    fields = list(fixture['fields'])
    if 'title' in fields:
        steps.extend([click_at('#page_title'), press('Control+a'), type_text(fixture['fields']['title'])])
        input_steps.append(len(steps))
    if fixture['kind'] == 'duplicate' or len(fields) > 1:
        steps.append(click_at('a[href="#metadata"]'))
        if fixture['kind'] == 'duplicate':
            steps.extend([click_at('#page_slug'), press('Control+a'), type_text('about-us')])
            input_steps.append(len(steps))
        for field in fields:
            if field != 'title':
                steps.extend([click_at('#page_' + field), press('Control+a'), type_text(fixture['fields'][field])])
                input_steps.append(len(steps))
    input_step = len(steps)
    steps.extend([scroll_to('input[name=continue]'), click_at('input[name=continue]')])
    submit_step = len(steps);navigation.append(submit_step)
    steps.append(press('Home'))
    review = [0, *input_steps, submit_step, len(steps)]
    if fixture['kind'] == 'edit':
        steps.append(press('Control+r'));review.append(len(steps))
        if len(fields) > 1:
            steps.append(click_at('a[href="#metadata"]'));review.append(len(steps))
    return SftFlow(steps, input_step, submit_step, tuple(review), tuple(navigation))


def litecart_reference(context, fixture):
    replay_flow(context, litecart_flow(fixture, ''))
    review = observed_review(context)
    context.result.update(agent_verdict=review['verdict'], observation=review['observation'],
                          evidence_steps=review['evidence_steps'], termination_reason='script_completed')


def wolf_reference(context, fixture):
    replay_flow(context, wolf_flow(fixture, ''))
    review = wolf_review(context)
    context.result.update(agent_verdict=review['verdict'], observation=review['observation'],
                          evidence_steps=review['evidence_steps'], termination_reason='script_completed')


def load_scenarios(path):
    import yaml
    from ..artifacts import digest
    scenarios = []
    for entry in yaml.safe_load(path.read_text())['scenarios']:
        case = entry['case_id']
        if case not in {'sweeper-105', 'sweeper-195'} or entry['data_split'] not in {'train', 'validation'}:
            raise ValueError('Unsupported LiteCart/Wolf scenario')
        fixture = dict(entry)
        if case == 'sweeper-105':
            countries = [entry['country']] if entry['country_code'] else []
            cities = [entry['city'] or '-- All Cities --'] if countries else []
            expected = json.dumps({key: entry[key] for key in ('code','name','description')} | {'countries':countries,'cities':cities}, ensure_ascii=False, sort_keys=True)
            fixture.update(username='tessa.quinn', password='bramble8oak', expected=expected)
            expected_verdict = 'fail' if entry['workflow'] == 'target' and entry['build'] == 'A' else 'pass'
            task = ('Open the Geo Zone editor from the Geo Zones list. ' if entry['start'] == 'list' else 'Use the displayed Geo Zone editor. ')
            task += ('Edit the existing zone Previous delivery zone. ' if entry.get('initial') else 'Create a new Geo Zone. ')
            task += 'Set ' + '; '.join(key.title()+' to '+repr(entry[key]) for key in entry['field_order']) + '. '
            if countries:
                task += f"Choose {entry['country']} in the pending Country dropdown" + (f" and type {entry['city']!r} into its City field" if entry['city'] else '') + '. Do not click Add. '
            else:
                task += 'Leave the pending Country unselected and do not click Add. '
            task += ('Click Save, reopen this same zone from its list row, and verify Code, Name and Description. '
                     'Inspect the saved country rows in the table body; the pending dropdown does not count. '
                     'Report pass if all requested fields and the requested country/city are saved (or no country row exists when none was selected), '
                     'fail if the completed save reopens with missing or different values, or blocked if the check cannot be completed.')
        else:
            fixture.update(username='niles.wade', password='corkLatch5!')
            expected = json.dumps(entry['fields'], ensure_ascii=False, sort_keys=True) if entry['kind'] == 'edit' else 'duplicate_slug_rejected'
            fixture['expected'] = expected
            expected_verdict = 'fail' if entry['kind'] == 'edit' and entry['build'] == 'A' else 'pass'
            if entry['kind'] == 'edit':
                task = 'Edit the existing About us page (page 3), preserving its slug about-us. '
                task += 'Set '+ '; '.join(key.title()+' to '+repr(value) for key,value in entry['fields'].items()) + '. '
                task += ('Use Metadata for the extra fields when needed. Click Save and Continue Editing. '
                         'Report fail if this existing page is rejected because its own slug already exists. '
                         'If Save succeeds, reload the editor and verify every requested field, reporting pass only when they persist. '
                         'Report blocked if the actual save or result cannot be observed.')
            else:
                task = 'Add a new child page under Home Page. '
                task += 'Set '+ '; '.join(key.title()+' to '+repr(value) for key,value in entry['fields'].items()) + '. '
                task += ('In Metadata, set Slug to about-us, which is already used by the About us sibling. '
                         'Click Save and Continue Editing. Report pass if this true duplicate slug is visibly rejected with the duplicate-slug error, '
                         'fail if a second page with that slug is saved, or blocked if you cannot complete the check.')
        fixture['matrix_expected'] = expected_verdict
        fingerprint = digest({key:value for key,value in entry.items() if key not in {'id','build','workflow','data_split'}})
        scenarios.append({'id':entry['id'],'case_id':case,'build':entry['build'],'workflow':entry['workflow'],
            'family':entry['family'],'data_split':entry['data_split'],'fingerprint':fingerprint,
            'public':{'task':task,'expected':expected},'fixture':fixture})
    if len({s['id'] for s in scenarios}) != len(scenarios):
        raise ValueError('Duplicate scenario IDs')
    return scenarios


def register():
    from ..runner import ADAPTERS, CaseAdapter
    from ..sft_flows import FLOWS
    ADAPTERS['sweeper-105'] = CaseAdapter(LitecartInstance, litecart_login, LitecartObserver, litecart_reference,
        scenario_observer=LitecartObserver)
    ADAPTERS['sweeper-195'] = CaseAdapter(WolfInstance, wolf_login, WolfObserver, wolf_reference,
        scenario_observer=WolfObserver, reviewer=wolf_review, oracle=wolf_oracle,
        review_profile='business_outcome_v1')
    FLOWS['sweeper-105'] = litecart_flow
    FLOWS['sweeper-195'] = wolf_flow
