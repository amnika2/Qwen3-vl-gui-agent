import csv
import hashlib
import json
import subprocess
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    checks = []
    for row in csv.DictReader((root / "cases/index.csv").open()):
        for label, field in (("base", "base_commit"), ("fixed", "reference_commit")):
            checkout = root / "vendor/apps" / row["slug"] / label
            actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
            names = subprocess.check_output(["git", "diff", "--name-only"], cwd=checkout, text=True, stderr=subprocess.PIPE).splitlines()
            changes = []
            for name in names:
                original = subprocess.check_output(["git", "show", "HEAD:" + name], cwd=checkout)
                current = (checkout / name).read_bytes()
                if original.replace(b"\r\n", b"\n") == current.replace(b"\r\n", b"\n"):
                    kind = "checkout_line_endings"
                elif row["case_id"] == "sweeper-005" and label == "base" and name == "public/css/app.css":
                    kind = "compiled_frontend"
                else:
                    kind = "modified_source"
                changes.append({"path": name, "kind": kind, "sha256": hashlib.sha256(current).hexdigest()})
            checks.append({"case_id": row["case_id"], "checkout": label, "commit_matches": actual == row[field],
                           "tracked_source_clean": not names, "changes": changes,
                           "business_source_unchanged": all(change["kind"] != "modified_source" for change in changes)})
    manifest = json.loads((root / "docs/data-manifest.json").read_text())
    data_matches = hashlib.sha256((root / "vendor/SWEeper-Bench/test.jsonl").read_bytes()).hexdigest() == manifest["sha256"]
    result = {"passed": data_matches and all(row["commit_matches"] and row["business_source_unchanged"] for row in checks),
              "original_dataset_sha256_matches": data_matches, "checkouts": checks}
    (root / "docs/source-verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"passed": result["passed"], "checkouts": len(checks), "original_dataset_sha256_matches": data_matches}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
