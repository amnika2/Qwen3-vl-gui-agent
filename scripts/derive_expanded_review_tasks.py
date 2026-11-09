import argparse
import json
from pathlib import Path

from guiqa.expanded_training_collection import derive_review_tasks


def main():
    parser = argparse.ArgumentParser(description="Derive Content-only review tasks from recorded supplementary trajectories.")
    parser.add_argument("--batch", type=Path, action="append", required=True)
    args = parser.parse_args()
    for batch in args.batch:
        print(json.dumps(derive_review_tasks(batch), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
