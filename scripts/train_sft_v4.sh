#!/usr/bin/env bash

cd "$(dirname "$0")/.." || exit 1
.model-venv/bin/python -m torch.distributed.run --standalone --nproc_per_node=2 \
  -m guiqa.train_sft --config configs/sft-train-v4.yaml
training_status=$?
.model-venv/bin/python - "$training_status" <<'PY'
import json
import sys
from pathlib import Path

Path("runs/sft-qwen3vl-10apps-v4/train-exit.json").write_text(
    json.dumps({"exit_code": int(sys.argv[1])}) + "\n")
PY
exit "$training_status"
