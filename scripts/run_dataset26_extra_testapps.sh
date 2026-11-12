#!/usr/bin/env bash
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/.."
case_id="$1"
case_port="$2"
task_dir="runs/dataset26-v1/jobs/extra-test-${case_id}"
mkdir -p "$task_dir"
.venv/bin/python scripts/collect_dataset26_webapps.py --case "$case_id" --port "$case_port" > "$task_dir/collection.log" 2>&1
job_exit=$?
.venv/bin/python - "$task_dir" "$case_id" "$job_exit" <<'PY'
import json,sys,time
from pathlib import Path
directory=Path(sys.argv[1])
root=Path('runs/dataset26-v1/collections')/sys.argv[2]
results=[json.loads(p.read_text()) for p in root.glob('d26-*-?/result.json')]
completed=[r for r in results if r.get('complete') and r.get('operation_samples')==5]
value={'case_id':sys.argv[2],'exit_code':int(sys.argv[3]),'finished_at':time.time(),
       'completed_trajectories':len(completed),'operation_samples':len(completed)*5,
       'review_samples':len(completed),'complete':len(completed)==20}
(directory/'finalizer.json').write_text(json.dumps(value,indent=2)+'\n')
PY
exit "$job_exit"
