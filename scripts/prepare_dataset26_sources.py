"""Download the exact approved source revisions without changing existing checkouts."""

import concurrent.futures
import hashlib
import json
import shutil
import tarfile
from pathlib import Path

import requests

from guiqa.dataset26_runtime import write_json


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runs/dataset26-v1"


def main():
    plan = json.loads((RUN / "plan.json").read_text())
    jobs = [(app, build, app[field]) for app in plan["applications"]
            for build, field in (("base", "base_commit"), ("fixed", "reference_commit"))]
    results, failed = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        pending = {pool.submit(prepare, *job): job for job in jobs}
        for future in concurrent.futures.as_completed(pending):
            app, build, _ = pending[future]
            try:
                row = future.result()
                results.append(row)
            except Exception as error:
                row = {"case_id": app["case_id"], "build": build, "error": str(error)}
                failed.append(row)
            print(json.dumps(row), flush=True)
            write_json(RUN / "source-preparation.json", {"complete": len(results) + len(failed) == len(jobs),
                       "prepared": results, "failed": failed})
    if failed:
        raise SystemExit(1)


def prepare(app, build, commit):
    target = ROOT / "vendor/apps" / app["slug"] / build
    if target.exists():
        return {"case_id": app["case_id"], "build": build, "commit": commit,
                "path": str(target), "existing": True}
    archive = ROOT / "vendor/source-archives" / f"{app['slug']}-{commit}.tar.gz"
    archive.parent.mkdir(exist_ok=True)
    if not archive.exists():
        cached = RUN / "webapps-source" / app["case_id"] / "source.tar.gz"
        if build == "base" and cached.exists():
            shutil.copyfile(cached, archive)
        else:
            repository = app["repo"].removeprefix("https://github.com/")
            response = requests.get(f"https://codeload.github.com/{repository}/tar.gz/{commit}", timeout=180)
            response.raise_for_status()
            archive.write_bytes(response.content)
    temporary = target.parent / (build + "-extract")
    temporary.mkdir(parents=True)
    with tarfile.open(archive) as bundle:
        bundle.extractall(temporary, filter="data")
    children = list(temporary.iterdir())
    if len(children) != 1 or not children[0].is_dir():
        raise ValueError("source archive does not have one repository root")
    children[0].rename(target)
    temporary.rmdir()
    row = {"case_id": app["case_id"], "build": build, "commit": commit,
           "path": str(target), "existing": False, "archive": str(archive),
           "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
    write_json(target.parent / f"{build}-source.json", row)
    return row


if __name__ == "__main__":
    main()
