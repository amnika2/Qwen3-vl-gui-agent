import argparse
import json
import re
import subprocess
from pathlib import Path

import yaml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sources = {"sweeper-bench": ("https://github.com/zlab-princeton/sweeper-bench", "96f86f2bb28f0de6c302c910aabe7baf3fb90b6a"),
               "cua-lite": ("https://github.com/cua-lite/cua-lite", "2af69268c0573f4c12aac391cc03125e6d5c7974")}
    for name, (url, commit) in sources.items():
        destination = root / "vendor" / name
        if not destination.exists():
            subprocess.run(["git", "clone", "--no-checkout", url, str(destination)], check=True)
            subprocess.run(["git", "checkout", "--detach", commit], cwd=destination, check=True)
        verify(destination, commit)
    files = [root / "cases" / args.case / "private.json"] if args.case else sorted((root / "cases").glob("*/private.json"))
    for file in files:
        row = json.loads(file.read_text())
        repository = root / "vendor/apps" / row["slug"]
        if not (repository / ".git").exists():
            repository.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", "--quiet"], cwd=repository, check=True)
            subprocess.run(["git", "remote", "add", "origin", row["repo"]], cwd=repository, check=True)
        for label, field in (("base", "base_commit"), ("fixed", "reference_commit")):
            checkout = repository / label
            commit = row[field]
            if not checkout.exists():
                subprocess.run(["git", "fetch", "--depth", "1", "origin", commit], cwd=repository, check=True)
                subprocess.run(["git", "worktree", "add", "--detach", label, commit], cwd=repository, check=True)
            verify(checkout, commit)
        if file.parent.name in ("sweeper-041", "sweeper-040", "sweeper-005", "sweeper-007", "sweeper-008"):
            fixtures(file.parent, row)


def verify(checkout, expected):
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
    if actual != expected:
        raise RuntimeError(f"{checkout}: expected {expected}, found {actual}")


def fixtures(directory, row):
    output = {}
    flows = yaml.safe_load(row["workflows"])["workflows"]
    for flow in flows:
        text = "\n".join(step if isinstance(step, str) else yaml.safe_dump(step) for step in flow["steps"])
        category = flow["category"]
        if row["case_id"] == "sweeper-007":
            username = re.search(r"username (\S+) and password", text)[1]
            password = re.search(r"and password (\S+) using", text)[1]
            item = {"username": username, "password": password, "object_id": 20,
                    "category": category,
                    "expected": "Renamed Smart Core4426" if category == "target" else "created"}
        elif row["case_id"] == "sweeper-008":
            username = re.search(r'Type "([^"]+)" into the Username field', text)[1]
            password = re.search(r'Type "([^"]+)" into the Password field', text)[1]
            expected = re.search(r'type exactly "([^"]+)"', text)[1] if category == "target" else re.search(r'Type "([^"]+)" into the Value textarea', text)[1]
            name = "greeting" if category == "target" else "hoursnote"
            item = {"username": username, "password": password, "name": name,
                    "object_id": "custom_" + name, "expected": expected,
                    "category": category}
        elif row["case_id"] == "sweeper-005":
            username = re.search(r"[Ss]ign in with email (\S+) and password", text)[1]
            password = re.search(r"and password (\S+)", text)[1].rstrip(".")
            item = {"username": username, "password": password,
                    "name": "Ceramic Table Lamp" if category == "target" else "Linen Throw Pillow",
                    "input_amount": "1.234.567,89" if category == "target" else "1.234,56",
                    "expected": "1234567.89" if category == "target" else "1234.56"}
        else:
            username = re.search(r"Type exactly (\S+) into the (?:user|Username) field", text)[1]
            password = re.search(r"Type exactly (\S+) into the (?:password|Password) field", text)[1]
            item = {"username": username, "password": password}
            if row["case_id"] == "sweeper-041":
                item.update(object_id=2 if category == "target" else 1,
                            expected="Saved Pen Name" if category == "target" else "Board Pen Name")
            else:
                field = "mobile_number" if category == "target" else "phone_number"
                item.update(object_id=2, field=field, selector="#" + field.replace("_", "-"),
                            expected="+15551234567" if category == "target" else "+15559876543")
        output[category] = item
    destination = directory / "fixtures.private.json"
    destination.write_text(json.dumps(output, indent=2) + "\n")
    destination.chmod(0o600)


if __name__ == "__main__":
    main()
