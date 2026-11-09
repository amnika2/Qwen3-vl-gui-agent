"""Build a frozen training-only procedural memory for paired GUI experiments."""

import argparse
import json
from pathlib import Path

from guiqa.e2e_memory import build_memory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--seed", type=Path, default=Path("configs/e2e-memory-seed.yaml"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = build_memory(args.root.resolve(), args.seed, args.output)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
