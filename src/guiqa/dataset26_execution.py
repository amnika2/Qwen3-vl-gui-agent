"""Run the frozen dataset26 tasks in fresh application instances."""

import copy
import hashlib
import json
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from .browser import VIEWPORT, execute, settle
from .dataset26_runtime import DatasetRecorder, write_json


class PreparedCase:
    def __init__(self, root, case_id, build, directory, port, workflow, index):
        self.root, self.directory = Path(root), Path(directory)
        self.case_id, self.build, self.workflow = case_id, build, workflow
        self.e2e = False
        plan = json.loads((self.root / 'runs/dataset26-v1/plan.json').read_text())
        app = next(app for app in plan['applications'] if app['case_id'] == case_id)
        self.category = app['category']
        self.source_commit = app['base_commit' if build == 'A' else 'reference_commit']
        self.index = selected_index(case_id, workflow, index)
        self.recorded = self.root / 'runs/dataset26-v1/collections' / case_id / f'd26-{case_id}-{self.index + 1:03d}-{build}'
        public = json.loads((self.recorded / 'result.json').read_text())
        self.task, self.task_family_id = public['task'], public['task_family_id']
        self.trace = [json.loads(line) for line in (self.recorded / 'trace.jsonl').read_text().splitlines()]
        self.original_url = json.loads((self.recorded / 'private/instance/instance.json').read_text())['url']
        self.fixture, self.spec, self.instance = instance_fixture(self, port)
        self.available_files = []
        self.upload_paths = {}
        for row in self.trace:
            action = row.get('action')
            if action and action['action'] == 'upload':
                source = Path(action['file'])
                target = self.directory / 'input-files' / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                self.upload_paths[str(source)] = str(target)
                self.available_files.append(str(target))
                self.fixture['upload_sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
                self.fixture['upload_filename'] = target.name

    def start(self):
        self.instance.start()
        seed_case(self)
        write_json(self.directory / 'private/dataset26-fixture.json', self.fixture)
        return self

    def setup(self, page):
        prepare_page(self, page)
        settle(page)

    def observer(self, page):
        from .dataset26_oracle import Dataset26Observer
        return Dataset26Observer(page, self.case_id, self.fixture)

    def reference(self, page, directory, observer=None):
        from . import dataset26_webapps as web
        if self.case_id in web.CASES:
            recorder = OracleReferenceRecorder(page, directory, self.task, observer,
                                               self.fixture.get('input_file'))
            web.FLOWS[self.case_id](recorder, self.instance.url, self.fixture)
            return recorder.history
        if self.case_id in {'sweeper-005', 'sweeper-087', 'sweeper-074'}:
            recorder = OracleReferenceRecorder(page, directory, self.task, observer)
            reference_test(self, recorder)
            return recorder.history
        directory = Path(directory)
        (directory / 'screenshots').mkdir(parents=True, exist_ok=True)
        emitted = []
        for row in self.trace:
            action = copy.deepcopy(row.get('action'))
            if action:
                if action['action'] == 'navigate':
                    action['url'] = action['url'].replace(self.original_url, self.instance.url)
                if action['action'] == 'upload':
                    action['file'] = self.upload_paths[action['file']]
                if observer is not None:
                    observer.before_action(action)
                execute_reference(page, action)
            settle(page)
            name = f"screenshots/{row['step']:03d}.png"
            page.screenshot(path=str(directory / name))
            if observer is not None:
                observer.capture(row['step'], name)
            emitted.append({'step': row['step'], 'action': action, 'screenshot': name,
                            'url': page.url, 'execution_error': None})
            (directory / 'trace.jsonl').write_text(''.join(json.dumps(item) + '\n' for item in emitted))
        return emitted

    def close(self):
        self.instance.close()


class OracleReferenceRecorder(DatasetRecorder):
    def __init__(self, page, directory, task, observer, input_file=None):
        self.observer, self.input_file = observer, input_file
        super().__init__(page, directory, {}, task)

    def capture(self, note='', action=None):
        step = super().capture(note, action)
        if self.observer is not None:
            self.observer.capture(step, f'screenshots/{step:03d}.png')
        return step

    def finish(self, *args, **kwargs):
        return None

    def step(self, action, wait_url=None, navigation=False):
        if self.observer is not None:
            self.observer.before_action(action)
        return super().step(action, wait_url=wait_url, navigation=navigation)

    def upload(self, locator, path):
        return super().upload(locator, self.input_file or path)


def prepare_case(root, case_id, build, directory, port, workflow='target', index=0):
    return PreparedCase(root, case_id, build, directory, port, workflow, index)


def selected_index(case_id, workflow, index):
    from . import dataset26_existing as existing
    from . import dataset26_webapps as web
    if case_id in existing.CASES:
        scenarios = [existing.make_spec(case_id, number + 1) for number in range(existing.CASES[case_id][2])]
    elif case_id in web.CASES:
        scenarios = web.build_scenarios(case_id)
    else:
        count = 15 if case_id == 'sweeper-001' else 10 if case_id in {'sweeper-087', 'sweeper-074'} else 25
        scenarios = [{'condition': 'preservation' if case_id == 'sweeper-087' and number >= 8 else 'target'} for number in range(count)]
    normal = {'preservation', 'integer_weight', 'replace_hostname', 'duplicate_sibling', 'second_up', 'ordinary_amount'}
    eligible = [number for number, scenario in enumerate(scenarios)
                if (scenario['condition'] in normal) == (workflow == 'preservation')]
    if not eligible:
        raise ValueError(f'{case_id} has no {workflow} condition in dataset26-v1')
    if index < 0 or index >= len(eligible):
        raise ValueError(f'{case_id} {workflow} index must be between 0 and {len(eligible) - 1}')
    return eligible[index]


def instance_fixture(prepared, port):
    from . import dataset26_existing as existing
    from . import dataset26_webapps as web
    from .dataset26_images import ImageInstance

    cid, root, build = prepared.case_id, prepared.root, prepared.build
    directory = prepared.directory / 'private/instance'
    if cid in existing.CASES:
        spec = existing.make_spec(cid, prepared.index + 1)
        app = spec['app']
        if app in {'opencart', 'phpipam', 'wolf'}:
            from .rl_targeted_collection import APPS, task_fixture
            fixture, _ = task_fixture({'app': app, 'fixture': spec['fixture']})
            instance = APPS[app][1](root, directory, build, port)
        elif app == 'akaunting':
            from .final_test_catalog import make_group
            from .final_test_collection import TestAkauntingInstance
            group = make_group(app, prepared.index + 50, 'preservation' if (prepared.index + 1) % 5 == 0 else 'target', 'saved', [build], False)
            fixture = json.loads((root / 'cases' / cid / 'fixtures.private.json').read_text())[group['workflow']]
            fixture.update(group['fixture'])
            instance = TestAkauntingInstance(root, directory, build, port)
            fixture['origin'] = instance.url
        else:
            from .training_instances import WondercmsInstance
            klass = {'wondercms': WondercmsInstance, 'dokuwiki': existing.NamespaceMediaInstance,
                     'flatpress': existing.PrettyUrlInstance}[app]
            instance = klass(root, directory, build, port)
            fixture = {**spec}
        return fixture, spec, instance
    if cid in web.CASES:
        spec = {**web.build_scenarios(cid)[prepared.index], 'build': build}
        _, fixture = web.task_fixture(spec)
        if cid == 'sweeper-151':
            from .dataset26_pocketbase import PocketbaseInstance
            instance = PocketbaseInstance(root, directory, build, port)
        elif cid == 'sweeper-025':
            from .dataset26_cockpit import CockpitInstance
            instance = CockpitInstance(root, directory, build, port)
        elif cid == 'sweeper-083':
            from .dataset26_ihatemoney import IhatemoneyInstance
            instance = IhatemoneyInstance(root, directory, build, port)
        elif cid == 'sweeper-037':
            from .dataset26_dotclear import DotclearInstance
            instance = DotclearInstance(root, directory, build, port)
        elif cid == 'sweeper-009':
            from .dataset26_answer import AnswerInstance
            instance = AnswerInstance(root, directory, build, port)
        else:
            instance = ImageInstance(root, directory, cid, build, port)
        return fixture, spec, instance
    index = prepared.index + 1
    spec = {'case_id': cid, 'index': index, 'instance_id': f'd26-{cid}-{index:03d}', 'build': build}
    fixture = {'index': index}
    environment = {}
    if cid == 'sweeper-087':
        environment = {'INACTIVE_USER_EMAIL': f'inactive{index:03d}@dataset26.test',
                       'INACTIVE_USER_NAME': f'Inactive account {index:03d}',
                       'INACTIVE_USER_PASSWORD': f'OakTable{index:03d}!',
                       'DEFAULT_ADMIN_EMAIL': f'admin{index:03d}@dataset26.test',
                       'DEFAULT_ADMIN_PASSWORD': f'NorthBridge{index:03d}!'}
        fixture.update(inactive=index <= 8,
                       email=environment['INACTIVE_USER_EMAIL' if index <= 8 else 'DEFAULT_ADMIN_EMAIL'],
                       password=environment['INACTIVE_USER_PASSWORD' if index <= 8 else 'DEFAULT_ADMIN_PASSWORD'])
    if cid == 'sweeper-001':
        from .dataset26_abantecart import AbantecartInstance
        instance = AbantecartInstance(root, directory, build, port)
    else:
        instance = ImageInstance(root, directory, cid, build, port, env=environment)
    if cid == 'sweeper-004': fixture['names'] = [f'Linen Apron {index:03d}', f'Clay Mug {index:03d}']
    if cid == 'sweeper-146': fixture['user'] = f'Tilda {index:03d}'
    if cid == 'sweeper-055': fixture.update(target=f'Please review the sample task {index:03d}.', retained=f'Keep the documentation reminder {index:03d}.')
    if cid == 'sweeper-049': fixture['filename'] = f'guest-evidence-{index:03d}.txt'
    if cid == 'sweeper-001': fixture['filename'] = f'id-photo-{index:03d}.txt'
    if cid == 'sweeper-074': fixture['note'] = f'KEEP_NOTE_{index:03d}'
    return fixture, spec, instance


def seed_case(prepared):
    from . import dataset26_existing as existing
    from . import dataset26_webapps as web
    from .dataset26_php_forms import mysql

    cid, fixture, instance, spec = prepared.case_id, prepared.fixture, prepared.instance, prepared.spec
    if cid in existing.CASES:
        if spec['app'] in {'opencart', 'phpipam', 'wolf'}:
            instance.seed_training_page(fixture)
        elif spec['app'] == 'wondercms':
            path = instance.app / 'data/database.js'
            database = json.loads(path.read_text())
            for key, name in zip(('0', '1'), spec['names']): database['config']['menuItems'][key]['name'] = name
            path.write_text(json.dumps(database, ensure_ascii=False))
        elif spec['app'] == 'dokuwiki': instance.seed_media(spec)
        elif spec['app'] == 'flatpress': instance.seed_posts(spec)
    elif cid in web.CASES:
        if not (prepared.e2e and cid == 'sweeper-027'):
            web.parameterize_fixture(instance, spec, fixture)
    elif cid == 'sweeper-004':
        mysql(instance, f"USE wickbin; UPDATE listings SET name='{fixture['names'][0]}' WHERE id=1; UPDATE listings SET name='{fixture['names'][1]}' WHERE id=2;")
    elif cid == 'sweeper-146':
        mysql(instance, f"USE phpservermon; UPDATE psm_users SET name='{fixture['user']}' WHERE user_name='tildaBenn';")
    elif cid == 'sweeper-055':
        mysql(instance, "USE flyspray; UPDATE flyspray_reminders SET reminder_message='" + fixture['target'] + "'; INSERT INTO flyspray_reminders(task_id,to_user_id,from_user_id,start_time,how_often,last_sent,reminder_message) SELECT task_id,to_user_id,from_user_id,start_time,how_often,last_sent,'" + fixture['retained'] + "' FROM flyspray_reminders LIMIT 1;")


def prepare_page(prepared, page):
    from . import dataset26_existing as existing
    from . import dataset26_webapps as web

    cid, fixture, instance, spec = prepared.case_id, prepared.fixture, prepared.instance, prepared.spec
    url = instance.url
    if cid in existing.CASES:
        if spec['app'] in {'opencart', 'phpipam', 'wolf'}:
            from .rl_targeted_collection import APPS
            APPS[spec['app']][2](page, url, fixture)
        elif spec['app'] == 'akaunting':
            from .browser import akaunting_login
            akaunting_login(page, url, fixture)
        elif spec['app'] == 'wondercms':
            from .training_cases import wondercms_login
            login = json.loads((prepared.root / 'cases/sweeper-196/fixtures.private.json').read_text())['target']
            wondercms_login(page, url, login)
        else:
            page.goto(url + ('/doku.php?id=start' if cid == 'sweeper-034' else '/'), wait_until='domcontentloaded')
    elif cid in web.CASES:
        web.prepare_login(page, url, cid)
    elif cid == 'sweeper-087':
        page.goto(url + '/sessions/login', wait_until='domcontentloaded')
        page.locator('#email').wait_for()
    elif cid == 'sweeper-049':
        page.goto(url, wait_until='domcontentloaded')
        page.get_by_text('Add files', exact=True).wait_for()
    elif cid == 'sweeper-074':
        page.goto(url, wait_until='domcontentloaded')
        page.goto(url + '/purchase', wait_until='domcontentloaded')
    elif cid == 'sweeper-001':
        page.goto(url + '/index.php?rt=account/login', wait_until='domcontentloaded')
        page.locator('input[name="loginname"]').fill('wrenHolt')
        page.locator('input[name="password"]').fill('peat8noll')
        page.get_by_role('button', name='Login', exact=False).click()
        page.wait_for_url(lambda value: 'account/login' not in str(value))
    elif cid == 'sweeper-004':
        page.goto(url, wait_until='domcontentloaded')
        for name, value in [('server', '127.0.0.1:' + str(instance.db_port)), ('username', 'dale.quist'), ('password', 'ironKettle3!'), ('db', 'wickbin')]:
            page.locator(f'input[name="auth[{name}]"]').fill(value)
        page.get_by_role('button', name='Login', exact=True).click()
        page.locator('a[href*="select=listings"]').first.wait_for()
    elif cid == 'sweeper-146':
        page.goto(url + '/index.php', wait_until='domcontentloaded')
        page.locator('#input-username').fill('jossPern')
        page.locator('#input-password').fill('crib8dusk')
        page.locator('button[type=submit]').click()
        page.locator('#input-username').wait_for(state='hidden')
    elif cid == 'sweeper-055':
        page.goto(url, wait_until='domcontentloaded')
        page.locator('#show_loginbox').click()
        page.locator('#lbl_user_name').fill('sasha.kirk')
        page.locator('#lbl_password').fill('peatCrest7!')
        page.locator('#login_button').click()
        page.locator('#logoutlink').wait_for()


def execute_reference(page, action):
    kind = action['action']
    if kind == 'navigate': page.goto(action['url'], wait_until='domcontentloaded')
    elif kind == 'select': page.locator(action['selector']).select_option(**{key: action[key] for key in ('label', 'value') if key in action})
    elif kind == 'upload':
        page.once('dialog', lambda dialog: dialog.accept())
        page.locator(action['selector']).set_input_files(action['file'])
    elif kind == 'type' and action.get('input_method') == 'keyboard': page.keyboard.type(action['text'], delay=25)
    else: execute(page, action)


def reference_test(prepared, recorder):
    page, fixture, url = recorder.page, prepared.fixture, prepared.instance.url
    if prepared.case_id == 'sweeper-005':
        recorder.fill(page.locator('input[name="name"]'), fixture['name'])
        recorder.fill(page.locator('input[name="sale_price"]'), fixture['input_amount'])
        recorder.fill(page.locator('input[name="purchase_price"]'), '100')
        recorder.click(page.get_by_role('button', name='Save', exact=True), wait_url=url + '/1/common/items')
        recorder.goto(url + '/1/common/items')
    elif prepared.case_id == 'sweeper-087':
        recorder.fill('#email', fixture['email'])
        recorder.fill('#password', fixture['password'])
        recorder.click('button[type="submit"]')
    else:
        from .dataset26_phpapps import grocy_choice
        grocy_choice(recorder, '#product_id_text_input', 'Cookies')
        recorder.fill('#display_amount', '2')
        if fixture.get('due_date'):
            recorder.fill('#best_before_date input.datetimepicker-input', '')
            recorder.step({'action': 'type', 'text': fixture['due_date'], 'input_method': 'keyboard'})
            recorder.press('Tab')
        else:
            recorder.click('label[for="datetimepicker-shortcut"]')
        grocy_choice(recorder, '#location_id_text_input', 'Candy cupboard')
        recorder.fill('#note', fixture['note'])
        recorder.click('#save-purchase-button')
        page.locator('.toast-success').wait_for()
        recorder.capture('Purchase confirmation')
        recorder.goto(url + '/transfer')
        grocy_choice(recorder, '#product_id_text_input', 'Cookies')
        recorder.select('#location_id_from', label='Candy cupboard (Default location)')
        recorder.fill('#display_amount', '1')
        recorder.select('#location_id_to', label='Pantry')
        recorder.click('#save-transfer-button')
        page.locator('.toast-success').wait_for()
        recorder.capture('Transfer confirmation')
        recorder.goto(url + '/stockentries')
        recorder.select('#location-filter', label='Pantry')


def prepare_e2e_case(root, condition, directory, port):
    prepared = prepare_case(root, condition['case_id'], condition['build'], directory, port)
    prepared.task = condition['task']
    prepared.e2e = True
    fixture = prepared.fixture
    cid = prepared.case_id
    if cid == 'sweeper-005':
        fixture.update(name='E2E Cedar desk lamp', input_amount='2.451.831,25', expected='2451831.25')
    elif cid == 'sweeper-192':
        fixture.update(email='jordan.lee@admin.com', height='177')
    elif cid == 'sweeper-009':
        fixture['title'] = fixture['original_title']
    elif cid == 'sweeper-027':
        fixture.update(first_marker='PAGE1_ONLY_MARKER', expected='LASTPAGE_ONLY_MARKER')
    elif cid == 'sweeper-043':
        fixture.update(pad='e2e-shift-brief', text='hey\n')
        source = Path(root) / 'runs/dataset26-v1/e2e/etherpad-legacy.etherpad'
        target = prepared.directory / 'input-files/etherpad-legacy.etherpad'
        shutil.copyfile(source, target)
        prepared.available_files = [str(target)]
        fixture['input_file'] = str(target)
        fixture['upload_sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
        fixture['upload_filename'] = target.name
    elif cid == 'sweeper-087':
        from .dataset26_images import ImageInstance
        fixture.update(email='inactive@example.com', password='oakTable#27x', inactive=True)
        prepared.instance = ImageInstance(Path(root), prepared.directory / 'private/instance', cid,
                                         condition['build'], port, env={
                                             'INACTIVE_USER_EMAIL': fixture['email'],
                                             'INACTIVE_USER_PASSWORD': fixture['password']})
    elif cid == 'sweeper-074':
        fixture.update(note='E2E_KEEP_NOTE', due_date='2030-12-31')
    return prepared
