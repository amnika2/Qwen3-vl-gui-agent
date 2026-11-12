"""Replay real GUI actions in fresh instances and calibrate independent oracles."""
import argparse
import hashlib
import json
import traceback
from pathlib import Path

from playwright.sync_api import sync_playwright

from guiqa.browser import VIEWPORT
from guiqa.dataset26_execution import prepare_case, prepare_e2e_case
from guiqa.dataset26_oracle import judge
from guiqa.dataset26_runtime import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', action='append')
    parser.add_argument('--build', action='append', choices=['A', 'B'])
    parser.add_argument('--port', type=int, default=14900)
    parser.add_argument('--e2e', action='store_true')
    parser.add_argument('--output', type=Path, default=Path('runs/dataset26-oracle-calibration-v1'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    cases = args.case or ['sweeper-005', 'sweeper-192', 'sweeper-045', 'sweeper-009', 'sweeper-027', 'sweeper-043', 'sweeper-087', 'sweeper-074']
    conditions = [json.loads(line) for line in (root / 'runs/dataset26-v1/e2e/conditions.jsonl').read_text().splitlines()]
    summary = {'complete': False, 'runs': [], 'failures': [], 'method': 'script_reference',
               'model_calls': 0, 'e2e_conditions': args.e2e,
               'source_sha256': {name: hashlib.sha256((root / 'src/guiqa' / name).read_bytes()).hexdigest()
                                 for name in ('dataset26_execution.py', 'dataset26_oracle.py', 'dataset26_runtime.py')}}
    args.output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            for cid in cases:
                for build in args.build or ['A', 'B']:
                    directory = args.output / (cid + '-' + build)
                    directory.mkdir(parents=True, exist_ok=True)
                    condition = next(row for row in conditions if row['case_id'] == cid and row['build'] == build) if args.e2e else None
                    prepared = (prepare_e2e_case(root, condition, directory, args.port) if args.e2e
                                else prepare_case(root, cid, build, directory, args.port))
                    observer = None
                    try:
                        prepared.start()
                        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
                        page.set_default_timeout(15000)
                        observer = prepared.observer(page)
                        prepared.setup(page)
                        prepared.reference(page, directory, observer)
                        verdict, reason = judge(observer)
                        write_json(directory / 'private/oracle.json', observer.data())
                        expected = 'fail' if build == 'A' else 'pass'
                        result = {'case_id': cid, 'build': build, 'task': prepared.task,
                                  'condition_id': condition['id'] if condition else None,
                                  'oracle_verdict': verdict, 'reason': reason, 'expected': expected, 'matches': verdict == expected}
                        write_json(directory / 'result.json', result)
                        summary['runs'].append(result)
                        print(json.dumps(result), flush=True)
                        page.close()
                    except Exception as error:
                        failure = {'case_id': cid, 'build': build, 'error': str(error)}
                        summary['failures'].append(failure)
                        (directory / 'error.txt').write_text(traceback.format_exc())
                        if observer is not None: write_json(directory / 'private/oracle.json', observer.data())
                        print(json.dumps(failure), flush=True)
                    finally:
                        prepared.close()
                        write_json(args.output / 'summary.json', summary)
    summary['complete'] = not summary['failures'] and all(row['matches'] for row in summary['runs'])
    write_json(args.output / 'summary.json', summary)
    write_json(args.output / 'finalizer.json', {'complete': summary['complete'], 'runs': len(summary['runs']), 'failures': len(summary['failures'])})
    raise SystemExit(0 if summary['complete'] else 1)


if __name__ == '__main__':
    main()
