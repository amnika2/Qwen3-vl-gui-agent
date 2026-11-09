"""Convert the frozen v3 readings and inspected preservation baselines to evidence_v2."""

import argparse
import json
from pathlib import Path

from guiqa.sft_v4 import generate_annotations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("datasets/sft-main-10apps-v3"))
    parser.add_argument("--output", type=Path, default=Path("docs/sft-v4-annotations.json"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    print(json.dumps(generate_annotations(root, root / args.source, root / args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
