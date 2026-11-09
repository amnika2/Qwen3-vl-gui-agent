from pathlib import Path

import yaml

from guiqa.final_test_catalog import build_catalog


if __name__ == "__main__":
    path = Path(__file__).resolve().parents[1] / "configs/review-final-test-expanded-v1.yaml"
    path.write_text(yaml.safe_dump(build_catalog(), allow_unicode=True, sort_keys=False))
    print(path)
