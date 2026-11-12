"""Download the official application images without requiring Docker privileges."""

import argparse
import concurrent.futures

from guiqa.dataset26_images import NEW_CASES, prepare_image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", type=int)
    parser.add_argument("--metadata-only", action="store_true")
    arguments = parser.parse_args()
    cases = arguments.case or NEW_CASES
    if arguments.metadata_only:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(prepare_image, f"sweeper-{case:03d}", False) for case in cases]
            for future in futures:
                print(future.result(), flush=True)
    else:
        for case in cases:
            print(prepare_image(f"sweeper-{case:03d}"), flush=True)


if __name__ == "__main__":
    main()
