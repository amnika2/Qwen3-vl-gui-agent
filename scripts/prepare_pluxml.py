import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    row = next(row for row in map(json.loads, (root / "vendor/SWEeper-Bench/test.jsonl").read_text().splitlines())
               if row["case_id"] == "sweeper-150")
    directory = root / "cases/sweeper-150"
    directory.mkdir(exist_ok=True)
    fixture = {"username": "ElenaVoss", "password": "quietMaple8!"}
    fixtures = {
        "target": {**fixture, "kind": "password", "object_id": 2, "test_username": "jonah19", "test_password": "riverGate6!",
                   "selector": "input[name='users[002][password]']", "input": "riverGate6!", "expected": "login=jonah19"},
        "preservation": {**fixture, "kind": "name", "object_id": 1, "test_username": "ElenaVoss", "test_password": "quietMaple8!",
                         "selector": "input[name='users[001][name]']", "input": "Elena Voss Desk", "expected": "Elena Voss Desk"}}
    public = {"case_id": row["case_id"], "start_path": "/core/admin/parametres_users.php", "workflows": {
        "target": {"task": "In Manage users, replace only jonah19's Password with riverGate6!, leaving ElenaVoss unchanged. Click Modify the users' list. Sign out, then sign in as jonah19 with riverGate6!. Report pass if that login reaches Administration, fail if it shows Incorrect login or password after the saved change, or blocked if the operation could not be completed.", "expected": "login=jonah19"},
        "preservation": {"task": "In Manage users, replace ElenaVoss's Name with Elena Voss Desk, leaving Password empty. Click Modify the users' list, reload and inspect the name. Sign out and sign back in as ElenaVoss with quietMaple8!, then reopen Manage users. Report pass if the name remains Elena Voss Desk and the original password still signs in, fail if either check fails, or blocked if you cannot complete the check.", "expected": "Elena Voss Desk"}}}
    for name, data in (("private.json", row), ("fixtures.private.json", fixtures), ("public.json", public)):
        path = directory / name
        path.write_text(json.dumps(data, indent=2) + "\n")
        if "private" in name:
            path.chmod(0o600)


if __name__ == "__main__":
    main()
