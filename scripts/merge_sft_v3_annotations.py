"""Combine the four independently checked application annotation batches."""

import argparse
from pathlib import Path

from guiqa.sft_v2 import checksum, read_json, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append")
    parser.add_argument("--output", type=Path, default=Path("docs/sft-v3-annotations.json"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    paths = args.input or [Path("docs") / name for name in (
        "sft-v3-cms-annotations.json", "sft-v3-lite-wolf-mantis-annotations.json",
        "sft-v3-mantis-annotations.json", "sft-v3-opencart-impress-phpipam-annotations.json")]
    annotations, sources = [], {}
    for relative in paths:
        path = root / relative
        sources[str(relative)] = checksum(path)
        for annotation in read_json(path)["annotations"]:
            annotations.append({**annotation, "annotation_source": str(relative)})
    write_json(root / args.output, {"version": "evidence_v1", "source_batches": sources,
                                  "annotations": annotations})
    print(f"Merged {len(annotations)} explicitly checked reviews into {args.output}")


if __name__ == "__main__":
    main()
