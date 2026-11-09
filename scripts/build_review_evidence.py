"""Build a review-only evidence_v1 preview from explicitly checked screenshot readings."""

import argparse
import json
from pathlib import Path

from guiqa.sft_review_evidence import build_review_dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("datasets/sft-main-10apps-v2"))
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest, verification = build_review_dataset(root, root / args.source, root / args.annotations, root / args.output)
    print(json.dumps({"manifest": manifest, "verification": verification}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
