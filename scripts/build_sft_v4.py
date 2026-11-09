"""Build v4 with unchanged operation supervision and atomic review evidence."""

import argparse
import json
from pathlib import Path

from guiqa.sft_v4 import build_dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("datasets/sft-main-10apps-v3"))
    parser.add_argument("--annotations", type=Path, default=Path("docs/sft-v4-annotations.json"))
    parser.add_argument("--output", type=Path, default=Path("datasets/sft-main-10apps-v4"))
    parser.add_argument("--curriculum", type=Path, action="append", default=[])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest, verification = build_dataset(root, root / args.source, root / args.annotations, root / args.output,
                                          [root / path for path in args.curriculum])
    print(json.dumps({"manifest": manifest, "verification": verification}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
