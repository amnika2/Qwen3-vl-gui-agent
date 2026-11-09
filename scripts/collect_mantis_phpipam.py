"""Collect diverse MantisBT/phpIPAM scenarios from verified native fixtures."""
import argparse
import json
import shutil
import tarfile
import uuid
from pathlib import Path

import yaml

from guiqa.apps.mantis_phpipam import register
from guiqa.artifacts import digest, source_digest, write_json
from guiqa.cli import load_config
from guiqa.runner import run_case
from guiqa.sft_export import export


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--method',choices=['script','sft'],default='sft')
    parser.add_argument('--scenario',action='append')
    parser.add_argument('--case',choices=['sweeper-107','sweeper-142'])
    parser.add_argument('--resume')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    register()
    directory,scenarios,method,config=prepare(root,args)
    snapshot(root,directory)
    completed=json.loads((directory/'completed.json').read_text())
    done={cell['scenario_id'] for cell in completed if cell['matrix_matches']}
    for scenario in scenarios:
        if scenario['id'] in done:continue
        port=13225 if scenario['case_id']=='sweeper-107' else 13226
        path,result=run_case(root,scenario['case_id'],scenario['build'],scenario['workflow'],method,config,
            port=port,experiment=directory.name,scenario=scenario)
        expected=scenario['fixture']['matrix_expected']
        cell={'case_id':scenario['case_id'],'scenario_id':scenario['id'],'build':scenario['build'],
              'workflow':scenario['workflow'],'data_split':scenario['data_split'],'method':method,
              'run_id':result['run_id'],'path':str(path.relative_to(root)),'expected':expected,
              'oracle_verdict':result['oracle_verdict'],'agent_verdict':result['agent_verdict'],
              'matrix_matches':result['oracle_verdict']==result['agent_verdict']==expected}
        completed.append(cell);write_json(directory/'completed.json',completed)
        print(json.dumps(cell,ensure_ascii=False),flush=True)
        if result['termination_reason']=='interrupted':break
    done={cell['scenario_id'] for cell in completed if cell['matrix_matches']}
    matrix={'planned':len(scenarios),'attempts':len(completed),'matched':len(done),
            'pending':[scenario['id'] for scenario in scenarios if scenario['id'] not in done],
            'passed':len(done)==len(scenarios)}
    write_json(directory/'matrix.json',matrix)
    if matrix['passed'] and args.output:
        if method!='sft':raise ValueError('only SFT collections can be exported')
        export(root,[directory.name],root/args.output,bundle_images=True)
    print(json.dumps({'experiment':directory.name,**matrix},ensure_ascii=False,indent=2))
    return 0 if matrix['passed'] else 4


def prepare(root,args):
    if args.resume:
        directory=root/'runs'/args.resume;schedule=json.loads((directory/'schedule.json').read_text())
        raw=yaml.safe_load((directory/'catalog.yaml').read_text())['scenarios']
        scenarios=[scenario(root,entry) for entry in raw if entry['id'] in schedule['scenario_ids']]
        return directory,scenarios,schedule['method'],schedule['config']
    catalog=root/'configs/sft-mantis-phpipam-scenarios.yaml'
    raw=yaml.safe_load(catalog.read_text())['scenarios']
    entries=[entry for entry in raw if (not args.case or entry['case_id']==args.case)
             and (not args.scenario or entry['id'] in args.scenario)]
    if not entries:raise ValueError('no selected scenarios')
    scenarios=[scenario(root,entry) for entry in entries]
    directory=root/'runs'/('mantis-phpipam-'+uuid.uuid4().hex[:8]);directory.mkdir()
    shutil.copy2(catalog,directory/'catalog.yaml');config=load_config(root/'configs/agent.yaml')
    write_json(directory/'schedule.json',{'experiment':directory.name,'method':args.method,'config':config,
        'scenario_ids':[entry['id'] for entry in entries],
        'planned':[{key:s[key] for key in ('id','case_id','build','workflow','data_split','family','fingerprint')} for s in scenarios]})
    write_json(directory/'completed.json',[])
    return directory,scenarios,args.method,config


def scenario(root,entry):
    case,workflow=entry['case_id'],entry['workflow']
    base=json.loads((root/'cases'/case/'fixtures.private.json').read_text())[workflow]
    fixture={**base,**entry['fixture']}
    fixture['matrix_expected']='fail' if entry['build']=='A' and workflow=='target' else 'pass'
    if case=='sweeper-107':
        fixture['expected']=fixture['summary']
        fields=fixture.get('field_order',['summary','description'])
        task='In the open MantisBT Report Issue form, choose Category General. '
        task+=f"Choose Product Version {fixture['version']!r}. " if fixture['version'] else 'Leave Product Version empty. '
        task+='Fill these fields in the stated order: '+', '.join(
            key+' = '+json.dumps(fixture[key],ensure_ascii=False) for key in fields)+'. '
        task+='Submit Issue, then inspect the response. Report pass if the new issue shows these values, fail if the valid submission is rejected with APPLICATION ERROR #29 for version, or blocked if the check cannot be completed.'
    else:
        wanted={key:fixture[key] for key in ('description','hostname')};wanted.update(fixture['changes'])
        fixture['expected']=json.dumps(wanted,sort_keys=True,ensure_ascii=False)
        task=f"On Customer 1, open Edit IP for {fixture['address']}. "
        edits=[]
        for key in fixture.get('field_order',list(fixture['changes'])):
            if key in fixture.get('append',{}):edits.append(f"append {fixture['append'][key]!r} to {key}")
            else:edits.append(f"set {key} to {fixture['changes'][key]!r}")
        task+='Then '+', then '.join(edits)+'. Preserve the other fields. Save with Edit IP, wait for the refreshed address table, and reopen the same address dialog. '
        task+='Report pass only if the requested values persisted, fail if a submitted edit reopens with different values, or blocked if you cannot complete the check.'
    fingerprint=digest({'case_id':case,'fixture':{key:value for key,value in fixture.items()
                       if key not in ('username','password','matrix_expected')}})
    return {**{key:entry[key] for key in ('id','case_id','build','workflow','data_split','family')},
            'fingerprint':fingerprint,'fixture':fixture,'public':{'task':task,'expected':fixture['expected']}}


def snapshot(root,directory):
    target=directory/'source-snapshots'/(source_digest(root)+'.tar.gz')
    if target.exists():return
    target.parent.mkdir(exist_ok=True)
    paths=list((root/'src/guiqa').rglob('*.py'))+[Path(__file__).resolve()]
    paths += [root/'cases'/case/name for case in ('sweeper-107','sweeper-142')
              for name in ('public.json','fixtures.private.json','private.json')]
    with tarfile.open(target,'w:gz') as archive:
        for path in paths:archive.add(path,arcname=str(path.relative_to(root)))


if __name__=='__main__':raise SystemExit(main())
