"""Prepare, run or summarize the paired GUI experiment."""

import argparse
from pathlib import Path

from guiqa.e2e_experiment import prepare, run_worker
from guiqa.e2e_report import summarize


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run", "report"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--suite", type=Path, default=ROOT / "configs/e2e-60.yaml")
    parser.add_argument("--worker", type=int, default=0)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--port", type=int, default=13401)
    args = parser.parse_args()
    directory = args.directory.resolve()
    if args.command == "prepare":
        protocol = prepare(ROOT, args.suite.resolve(), directory)
        print(f"Prepared {len(protocol['cells'])} cells in {directory}")
    elif args.command == "run":
        return run_worker(ROOT, directory, args.worker, args.workers, args.port)
    else:
        print(summarize(ROOT, directory))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
