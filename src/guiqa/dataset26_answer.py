"""Run the pinned Apache Answer binary with the official install inputs."""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import requests
import yaml

from .dataset26_images import CACHE, native_go_binary


class AnswerInstance:
    def __init__(self, root, directory, build, port):
        self.root, self.directory = Path(root).resolve(), Path(directory).resolve()
        self.build, self.port = build, port
        self.url = f"http://127.0.0.1:{port}"
        self.process = None
        self.handle = None

    def start(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        case = next(row for row in map(json.loads, (self.root / "vendor/SWEeper-Bench/test.jsonl").read_text().splitlines())
                    if row["case_id"] == "sweeper-009")
        commit = case["base_commit" if self.build == "A" else "reference_commit"]
        self.app = self.root / "vendor/apps/answer" / ("base" if self.build == "A" else "fixed")
        rootfs = CACHE / "sweeper-009/rootfs"
        shutil.copytree(rootfs / "opt/product-deps/ui-build", self.app / "ui/build", dirs_exist_ok=True)
        binary = native_go_binary("sweeper-009", commit, self.app, rootfs)
        data = self.directory / "data"
        for name in ("uploads", "i18n", "cache", "sqlite3", "conf"):
            (data / name).mkdir(parents=True, exist_ok=True)
        shutil.copytree(rootfs / "opt/product-deps/i18n", data / "i18n", dirs_exist_ok=True)
        self.live_db = str(data / "sqlite3/answer.db")
        environment = {**os.environ, "AUTO_INSTALL": "true", "DB_TYPE": "sqlite3", "DB_FILE": self.live_db,
                       "LANGUAGE": "en-US", "SITE_NAME": "Community Answers", "SITE_URL": self.url,
                       "CONTACT_EMAIL": "siteadmin@communityanswers.local", "ADMIN_NAME": "siteadmin",
                       "ADMIN_PASSWORD": "SiteAdmin#2024", "ADMIN_EMAIL": "siteadmin@communityanswers.local",
                       "EXTERNAL_CONTENT_DISPLAY": "always_display", "SITE_ADDR": f"127.0.0.1:{self.port}",
                       "INSTALL_PORT": str(self.port + 1), "LOG_LEVEL": "WARN"}
        with (self.directory / "seed.log").open("w") as handle:
            for command in ("init", "upgrade"):
                subprocess.run([str(binary), command, "-C", str(data)], cwd=self.app, env=environment,
                               stdout=handle, stderr=handle, check=True)
        config = data / "conf/config.yaml"
        settings = yaml.safe_load(config.read_text())
        settings["server"]["http"]["addr"] = f"127.0.0.1:{self.port}"
        settings["swaggerui"]["address"] = ":" + str(self.port)
        config.write_text(yaml.safe_dump(settings, sort_keys=False))
        with sqlite3.connect(self.live_db) as database:
            seo = json.loads(database.execute("SELECT content FROM site_info WHERE type='seo' LIMIT 1").fetchone()[0])
            seo["permalink"] = 4
            database.execute("UPDATE site_info SET content=? WHERE type='seo'", (json.dumps(seo),))
        self.handle = (self.directory / "server.log").open("w")
        self.process = subprocess.Popen([str(binary), "run", "-C", str(data)], cwd=self.app,
                                        env=environment, stdout=self.handle, stderr=self.handle)
        session = requests.Session()
        session.trust_env = False
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if session.get(self.url, timeout=1).status_code == 200:
                    break
            except requests.ConnectionError:
                time.sleep(0.1)
        session.close()
        helper = CACHE / "sweeper-009/helpers/product-yarn"
        (self.directory / "instance.json").write_text(json.dumps({
            "case_id": "sweeper-009", "build": self.build, "commit": commit, "url": self.url,
            "source": str(self.app), "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "compilation": json.loads((binary.parent / "provenance.json").read_text()),
            "seed_source": str(helper), "seed_sha256": hashlib.sha256(helper.read_bytes()).hexdigest(),
            "pid": self.process.pid}, indent=2) + "\n")
        return self

    def close(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=10)
        if self.handle is not None:
            self.handle.close()
