"""Collect the pinned LiteCart and Wolf CMS form scenarios."""
import argparse
import hashlib
import json
import shutil
import tarfile
import uuid
from pathlib import Path

from guiqa.artifacts import write_json
from guiqa.cli import load_config
from guiqa.runner import run_case
from guiqa.apps.litecart_wolf import load_scenarios,register


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--catalog',type=Path,default=Path('configs/sft-litecart-wolf-scenarios.yaml'))
    parser.add_argument('--method',choices=['script','sft'],default='sft')
    parser.add_argument('--scenario',action='append')
    parser.add_argument('--case')
    parser.add_argument('--resume')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    register()
    if args.resume:
        directory=root/'runs'/args.resume;schedule=json.loads((directory/'schedule.json').read_text())
        all_scenarios=load_scenarios(directory/'catalog.yaml');ids=set(schedule['scenario_ids'])
        scenarios=[s for s in all_scenarios if s['id'] in ids];config=schedule['config'];method=schedule['method']
    else:
        scenarios=load_scenarios(root/args.catalog)
        if args.case:scenarios=[s for s in scenarios if s['case_id']==args.case]
        if args.scenario:scenarios=[s for s in scenarios if s['id'] in args.scenario]
        if not scenarios:raise ValueError('No scenarios selected')
        config=load_config(root/'configs/agent.yaml');method=args.method
        directory=root/'runs'/('litecart-wolf-'+uuid.uuid4().hex[:8]);directory.mkdir()
        shutil.copy2(root/args.catalog,directory/'catalog.yaml')
        write_json(directory/'schedule.json',{'experiment':directory.name,'scenario_ids':[s['id'] for s in scenarios],
            'planned':[{key:s[key] for key in ('id','case_id','build','workflow','family','data_split','fingerprint')} for s in scenarios],
            'config':config,'method':method})
        write_json(directory/'completed.json',[])
    app=root/'src/guiqa/apps/litecart_wolf.py';app_hash=hashlib.sha256(app.read_bytes()).hexdigest()
    snapshot=directory/('source-'+app_hash[:12]+'.tar.gz')
    if not snapshot.exists():
        with tarfile.open(snapshot,'w:gz') as archive:
            for path in list((root/'src/guiqa').rglob('*.py'))+[root/'scripts/collect_litecart_wolf.py']:
                archive.add(path,arcname=str(path.relative_to(root)))
    completed=json.loads((directory/'completed.json').read_text())
    good={c['scenario_id'] for c in completed if c['matrix_matches']}
    for scenario in scenarios:
        if scenario['id'] in good:continue
        port=13221 if scenario['case_id']=='sweeper-105' else 13222
        path,result=run_case(root,scenario['case_id'],scenario['build'],scenario['workflow'],method,config,
            port=port,experiment=directory.name,scenario=scenario)
        expected=scenario['fixture']['matrix_expected']
        cell={'scenario_id':scenario['id'],'case_id':scenario['case_id'],'build':scenario['build'],'workflow':scenario['workflow'],
            'data_split':scenario['data_split'],'method':method,'run_id':result['run_id'],'path':str(path.relative_to(root)),
            'expected':expected,'oracle_verdict':result['oracle_verdict'],'agent_verdict':result['agent_verdict'],
            'matrix_matches':result['oracle_verdict']==result['agent_verdict']==expected,'adapter_sha256':app_hash}
        completed.append(cell);write_json(directory/'completed.json',completed);print(json.dumps(cell,ensure_ascii=False),flush=True)
        if result['termination_reason']=='interrupted':break
    good={c['scenario_id'] for c in completed if c['matrix_matches']}
    matrix={'planned':len(scenarios),'attempts':len(completed),'successful_scenarios':len(good),
        'passed':len(good)==len(scenarios),'pending_scenarios':[s['id'] for s in scenarios if s['id'] not in good]}
    write_json(directory/'matrix.json',matrix);print(json.dumps({'experiment':directory.name,**matrix},ensure_ascii=False,indent=2))
    return 0 if matrix['passed'] else 4


if __name__=='__main__':raise SystemExit(main())
