"""Run pinned Dotclear with the pinned Clearbricks and the official SQLite seed."""

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import requests


class DotclearInstance:
    def __init__(self, root, directory, build, port):
        self.root, self.directory = Path(root).resolve(), Path(directory).resolve()
        self.build, self.port = build, port
        self.url = f"http://127.0.0.1:{port}"
        self.app = self.directory / "workspace"
        self.live_db = str(self.app / "db/dotclear.db")
        self.process = None
        self.handle = None

    def start(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        case = next(row for row in map(json.loads, (self.root / "vendor/SWEeper-Bench/test.jsonl").read_text().splitlines())
                    if row["case_id"] == "sweeper-037")
        commit = case["base_commit" if self.build == "A" else "reference_commit"]
        source = self.root / "vendor/apps/dotclear" / ("base" if self.build == "A" else "fixed")
        shutil.copytree(source, self.app, ignore=shutil.ignore_patterns(".git"), symlinks=True)
        clearbricks = self.root / "vendor/runtime/dataset26-clearbricks"
        shutil.copytree(clearbricks / "source", self.app / "inc/libs/clearbricks", dirs_exist_ok=True, symlinks=True)
        for name in ("db", "cache", "public"):
            (self.app / name).mkdir(exist_ok=True)
        (self.directory / "sessions").mkdir()
        extension = self.root / "vendor/runtime/dataset26-php-sqlite/usr/lib/php/20230831"
        self.php = [str(self.root / "vendor/runtime/php"), "-d", "extension=" + str(extension / "pdo_sqlite.so"),
                    "-d", "extension=" + str(extension / "sqlite3.so"), "-d", "display_errors=0",
                    "-d", "log_errors=1", "-d", "error_reporting=22527",
                    "-d", "session.save_path=" + str(self.directory / "sessions")]
        environment = {**os.environ, "DOTCLEAR_APP_ROOT": str(self.app), "DOTCLEAR_PUBLIC_HOST": "127.0.0.1",
                       "DOTCLEAR_PUBLIC_PORT": str(self.port), "DOTCLEAR_ADMIN_LOGIN": "nell.briggs",
                       "DOTCLEAR_ADMIN_PASSWORD": "amberWick8!", "DOTCLEAR_ADMIN_EMAIL": "nell.briggs@admin.com"}
        helpers = self.root / "runs/dataset26-v1/images/sweeper-037/helpers"
        with (self.directory / "seed.log").open("w") as handle:
            for name in ("bootstrap-install.php", "seed-posts.php"):
                subprocess.run(self.php + [str(helpers / name)], cwd=self.app, env=environment,
                               stdout=handle, stderr=handle, check=True)
        shutil.copy2(helpers / "router.php", self.app / "router.php")
        self.handle = (self.directory / "server.log").open("w")
        self.process = subprocess.Popen(self.php + ["-S", f"127.0.0.1:{self.port}", "-t", str(self.app),
                                                    str(self.app / "router.php")],
                                        env=environment, stdout=self.handle, stderr=self.handle)
        session = requests.Session()
        session.trust_env = False
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if session.get(self.url + "/admin/auth.php", timeout=1).status_code == 200:
                    break
            except requests.ConnectionError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Dotclear did not serve its login page; see server.log")
        session.close()
        (self.directory / "instance.json").write_text(json.dumps({
            "case_id": "sweeper-037", "build": self.build, "commit": commit, "url": self.url,
            "source": str(source), "runtime": "existing PHP 8.3.6 plus matching SQLite extension",
            "clearbricks": json.loads((clearbricks / "source.json").read_text()),
            "seed_sources": {name: hashlib.sha256((helpers / name).read_bytes()).hexdigest()
                             for name in ("bootstrap-install.php", "seed-posts.php", "router.php")},
            "pid": self.process.pid}, indent=2) + "\n")
        return self

    def close(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=10)
        if self.handle is not None:
            self.handle.close()
