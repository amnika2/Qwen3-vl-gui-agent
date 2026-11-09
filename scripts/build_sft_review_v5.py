"""Build the compact review SFT batch and fixed operation replay."""

import argparse
import json
from pathlib import Path

from guiqa.sft_review_v5 import build_dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reviews", type=Path, default=Path("datasets/rl-review-pilot-v2"))
    parser.add_argument("--operations", type=Path, default=Path("datasets/sft-main-10apps-v4"))
    parser.add_argument("--pixel-audit", type=Path, default=Path("docs/sft-review-v5-pixel-audit.json"))
    parser.add_argument("--output", type=Path, default=Path("datasets/sft-review-v5"))
    parser.add_argument("--replay-count", type=int, default=145)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest, verification = build_dataset(root, root / args.reviews, root / args.operations,
                                          root / args.pixel_audit, root / args.output, args.replay_count)
    print(json.dumps({"manifest": manifest, "verification": verification}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
