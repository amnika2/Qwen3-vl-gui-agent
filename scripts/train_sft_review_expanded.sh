#!/usr/bin/env bash

cd "$(dirname "$0")/.." || exit 1
export CUDA_VISIBLE_DEVICES=2,3
export OMP_NUM_THREADS=4
export TOKENIZERS_PARALLELISM=false

.model-venv/bin/python -m torch.distributed.run --standalone --nproc_per_node=2 \
  -m guiqa.train_sft --config configs/sft-train-review-expanded-v1.yaml "$@"
training_status=$?
.venv/bin/python - "$training_status" <<'PY'
import json
import sys
from pathlib import Path

Path("runs/sft-qwen3vl-review-expanded-v1/train-exit.json").write_text(
    json.dumps({"exit_code": int(sys.argv[1])}) + "\n")
PY
exit "$training_status"
