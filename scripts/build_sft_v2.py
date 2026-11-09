"""Build an isolated v2 SFT dataset using only recorded browser evidence."""
import argparse
import json
from pathlib import Path

from guiqa.sft_v2 import build_dataset, verify_dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("datasets/sft-main-10apps-v1"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--extra-experiment", action="append", default=[])
    parser.add_argument("--extra-review-jsonl", type=Path, action="append", default=[])
    parser.add_argument("--visible-proofs", type=Path, action="append", default=[])
    parser.add_argument("--prefix-annotations", type=Path, action="append", default=[])
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.verify_only:
        print(json.dumps(verify_dataset(root, root / args.output), indent=2))
        return
    manifest, verification = build_dataset(root, root / args.source, root / args.output,
                                         args.extra_experiment, args.extra_review_jsonl,
                                         args.visible_proofs, args.prefix_annotations)
    print(json.dumps({key: manifest[key] for key in ("samples", "trajectory_count", "sample_kinds", "review_labels",
                     "train_samples", "validation_samples")}, indent=2))
    print(json.dumps(verification, indent=2))


if __name__ == "__main__":
    main()
