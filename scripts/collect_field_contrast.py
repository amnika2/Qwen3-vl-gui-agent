"""Collect adjacent-field contrasts with real submits and same-object reopening."""
import argparse
import hashlib
import json
import shutil
import tarfile
import uuid
from pathlib import Path

import yaml

from guiqa.apps.litecart_wolf import load_scenarios, register, wolf_flow
from guiqa.artifacts import write_json
from guiqa.cli import load_config
from guiqa.runner import run_case
from guiqa.sft_flows import FLOWS, SftFlow, click_at, click_locator


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--catalog', type=Path, default=Path('configs/sft-field-contrast-scenarios.yaml'))
    parser.add_argument('--resume')
    parser.add_argument('--stop-after', type=int)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    register()
    FLOWS['sweeper-195'] = wolf_field_contrast_flow
    if args.resume:
        directory = root / 'runs' / args.resume
        schedule = json.loads((directory / 'schedule.json').read_text())
        config = schedule['config']
    else:
        directory = root / 'runs' / ('field-contrast-' + uuid.uuid4().hex[:8])
        directory.mkdir()
        shutil.copy2(root / args.catalog, directory / 'catalog.yaml')
        config = load_config(root / 'configs/agent.yaml')
        write_json(directory / 'completed.json', [])
    scenarios = field_scenarios(directory / 'catalog.yaml')
    if not args.resume:
        write_json(directory / 'schedule.json', {
            'experiment': directory.name, 'config': config, 'method': 'sft',
            'planned': [{key: scene[key] for key in ('id', 'case_id', 'build', 'workflow',
                'family', 'data_split', 'fingerprint')} for scene in scenarios]})
    script = Path(__file__)
    script_hash = hashlib.sha256(script.read_bytes()).hexdigest()
    snapshot = directory / ('source-' + script_hash[:12] + '.tar.gz')
    if not snapshot.exists():
        with tarfile.open(snapshot, 'w:gz') as archive:
            for path in [*(root / 'src/guiqa').rglob('*.py'), script]:
                archive.add(path, arcname=str(path.relative_to(root)))
    completed = json.loads((directory / 'completed.json').read_text())
    done = {cell['scenario_id'] for cell in completed}
    new_runs = 0
    for scene in scenarios:
        if scene['id'] in done:
            continue
        port = 13221 if scene['case_id'] == 'sweeper-105' else 13222
        path, result = run_case(root, scene['case_id'], scene['build'], scene['workflow'],
            'sft', config, port=port, experiment=directory.name, scenario=scene)
        expected = scene['fixture']['matrix_expected']
        cell = {'scenario_id': scene['id'], 'case_id': scene['case_id'], 'build': scene['build'],
            'workflow': scene['workflow'], 'data_split': scene['data_split'], 'method': 'sft',
            'run_id': result['run_id'], 'path': str(path.relative_to(root)), 'expected': expected,
            'oracle_verdict': result['oracle_verdict'], 'agent_verdict': result['agent_verdict'],
            'matrix_matches': result['oracle_verdict'] == result['agent_verdict'] == expected,
            'collector_sha256': script_hash}
        completed.append(cell)
        write_json(directory / 'completed.json', completed)
        print(json.dumps(cell, ensure_ascii=False), flush=True)
        new_runs += 1
        if result['termination_reason'] == 'interrupted' or new_runs == args.stop_after:
            break
    matched = {cell['scenario_id'] for cell in completed if cell['matrix_matches']}
    matrix = {'planned': len(scenarios), 'attempts': len(completed),
        'successful_scenarios': len(matched), 'passed': len(matched) == len(scenarios),
        'pending_scenarios': [scene['id'] for scene in scenarios if scene['id'] not in matched]}
    write_json(directory / 'matrix.json', matrix)
    print(json.dumps({'experiment': directory.name, **matrix}, ensure_ascii=False), flush=True)


def field_scenarios(path):
    scenes = load_scenarios(path)
    entries = {entry['id']: entry for entry in yaml.safe_load(path.read_text())['scenarios']}
    for scene in scenes:
        scene['public']['task'] = entries[scene['id']]['task']
    return scenes


def wolf_field_contrast_flow(fixture, build):
    base = wolf_flow(fixture, build)
    steps = list(base.steps[:base.submit_step + 1])
    review = [step for step in base.review_steps if step <= len(steps)]
    navigation = [step for step in base.navigation_steps if step <= len(steps)]
    steps.append(click_locator(lambda page: page.locator('a[href$="/admin/page"]').first))
    navigation.append(len(steps))
    steps.append(click_locator(lambda page: page.locator('a[href$="page/edit/3"]').first))
    navigation.append(len(steps))
    steps.append(click_at('a[href="#metadata"]'))
    review.append(len(steps))
    return SftFlow(steps, base.input_step, base.submit_step, tuple(review), tuple(navigation))


if __name__ == '__main__':
    main()
