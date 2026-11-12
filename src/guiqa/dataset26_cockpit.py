"""Run the pinned Cockpit PHP application with its official SQLite seed."""

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import requests


class CockpitInstance:
    def __init__(self, root, directory, build, port):
        self.root, self.directory = Path(root).resolve(), Path(directory).resolve()
        self.build, self.port = build, port
        self.url = f"http://127.0.0.1:{port}"
        self.app = self.directory / "workspace"
        self.live_app = str(self.app)
        self.process = None
        self.handle = None

    def start(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        case = next(row for row in map(json.loads, (self.root / "vendor/SWEeper-Bench/test.jsonl").read_text().splitlines())
                    if row["case_id"] == "sweeper-025")
        commit = case["base_commit" if self.build == "A" else "reference_commit"]
        source = self.root / "vendor/apps/cockpit" / ("base" if self.build == "A" else "fixed")
        shutil.copytree(source, self.app, ignore=shutil.ignore_patterns(".git"))
        for name in ("data", "cache", "tmp", "thumbs", "uploads", "singleton"):
            (self.app / "storage" / name).mkdir(exist_ok=True)
        (self.directory / "sessions").mkdir()
        extension = self.root / "vendor/runtime/dataset26-php-sqlite/usr/lib/php/20230831"
        self.php = [str(self.root / "vendor/runtime/php"), "-d", "extension=" + str(extension / "pdo_sqlite.so"),
                    "-d", "extension=" + str(extension / "sqlite3.so"), "-d", "display_errors=0",
                    "-d", "log_errors=1", "-d", "error_reporting=22527",
                    "-d", "session.save_path=" + str(self.directory / "sessions")]
        self.environment = {**os.environ, "COCKPIT_APP_ROOT": str(self.app),
                            "COCKPIT_SEED_HOST": f"127.0.0.1:{self.port}"}
        helpers = self.root / "runs/dataset26-v1/images/sweeper-025/helpers"
        seed = self.run(["php", str(helpers / "seed-cockpit.php")])
        (self.directory / "seed.log").write_text(seed.stdout + seed.stderr)
        self.handle = (self.directory / "server.log").open("w")
        self.process = subprocess.Popen(self.php + ["-S", f"127.0.0.1:{self.port}", "-t", str(self.app),
                                                    str(helpers / "cockpit-router.php")],
                                        env=self.environment, stdout=self.handle, stderr=self.handle)
        session = requests.Session()
        session.trust_env = False
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if session.get(self.url + "/auth/login", timeout=1).status_code == 200:
                    break
            except requests.ConnectionError:
                time.sleep(0.1)
        session.close()
        (self.directory / "instance.json").write_text(json.dumps({
            "case_id": "sweeper-025", "build": self.build, "commit": commit, "url": self.url,
            "source": str(source), "runtime": "existing PHP 8.3.6 plus matching SQLite extension",
            "seed_source": str(helpers / "seed-cockpit.php"),
            "seed_sha256": hashlib.sha256((helpers / "seed-cockpit.php").read_bytes()).hexdigest(),
            "pid": self.process.pid}, indent=2) + "\n")
        return self

    def run(self, arguments):
        return subprocess.run(self.php + arguments[1:], cwd=self.app, env=self.environment,
                              text=True, capture_output=True, check=True)

    def close(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=10)
        if self.handle is not None:
            self.handle.close()
