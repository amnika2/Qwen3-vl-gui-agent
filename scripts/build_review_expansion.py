"""Build expanded review SFT and a separate frozen final-test package."""

import argparse
import json
from pathlib import Path

from guiqa.review_expansion import build_packages


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, default=root / "datasets/sft-review-expanded-v1")
    parser.add_argument("--test-output", type=Path, default=root / "datasets/review-final-test-expanded-v1")
    parser.add_argument("--rl-output", type=Path, default=root / "datasets/rl-review-expanded-v1")
    args = parser.parse_args()
    print(json.dumps(build_packages(root, args.batch, args.output, args.test_output, args.rl_output),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
