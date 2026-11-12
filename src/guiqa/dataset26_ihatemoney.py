"""Run the pinned I hate money application and the original project seed."""

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import requests


class IhatemoneyInstance:
    def __init__(self, root, directory, build, port):
        self.root, self.directory = Path(root).resolve(), Path(directory).resolve()
        self.build, self.port = build, port
        self.url = f"http://127.0.0.1:{port}"
        self.app = self.directory / "workspace"
        self.process = None
        self.handle = None

    def start(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        case = next(row for row in map(json.loads, (self.root / "vendor/SWEeper-Bench/test.jsonl").read_text().splitlines())
                    if row["case_id"] == "sweeper-083")
        commit = case["base_commit" if self.build == "A" else "reference_commit"]
        source = self.root / "vendor/apps/ihatemoney" / ("base" if self.build == "A" else "fixed")
        shutil.copytree(source, self.app, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        helpers = self.root / "runs/dataset26-v1/images/sweeper-083/helpers"
        data = self.directory / "data"
        data.mkdir()
        config = self.directory / "ihatemoney.cfg"
        config.write_text((helpers / "ihatemoney.cfg").read_text().replace(
            "sqlite:////tmp/ihatemoney-data/ihatemoney.db", "sqlite:///" + str(data / "ihatemoney.db")))
        self.python = str(self.root / "vendor/runtime/dataset26-ihatemoney-venv/bin/python")
        self.environment = {**os.environ, "PYTHONPATH": str(self.app),
                            "IHATEMONEY_SETTINGS_FILE_PATH": str(config),
                            "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
                            "http_proxy": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9",
                            "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
        seeded = subprocess.run([self.python, str(helpers / "ihatemoney-seed.py")], cwd=self.app,
                                env=self.environment, capture_output=True, text=True, check=True)
        (self.directory / "seed.log").write_text(seeded.stdout + seeded.stderr)
        self.handle = (self.directory / "server.log").open("w")
        self.process = subprocess.Popen([self.python, "-m", "flask", "--app", "ihatemoney.wsgi", "run",
                                         "--host=127.0.0.1", "--port=" + str(self.port)], cwd=self.app,
                                        env=self.environment, stdout=self.handle, stderr=self.handle)
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
        (self.directory / "instance.json").write_text(json.dumps({
            "case_id": "sweeper-083", "build": self.build, "commit": commit, "url": self.url,
            "source": str(source), "runtime": self.python,
            "seed_source": str(helpers / "ihatemoney-seed.py"),
            "seed_sha256": hashlib.sha256((helpers / "ihatemoney-seed.py").read_bytes()).hexdigest(),
            "pid": self.process.pid}, indent=2) + "\n")
        return self

    def close(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=10)
        if self.handle is not None:
            self.handle.close()
