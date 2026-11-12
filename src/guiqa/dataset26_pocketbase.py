"""Run the pinned PocketBase binary and the official collection seed locally."""

import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path

import requests

from .dataset26_images import CACHE, native_go_binary


class PocketbaseInstance:
    def __init__(self, root, directory, build, port):
        self.root, self.directory = Path(root).resolve(), Path(directory).resolve()
        self.build, self.port = build, port
        self.url = f"http://127.0.0.1:{port}"
        self.process = None
        self.handle = None

    def start(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        row = next(row for row in map(json.loads, (self.root / "vendor/SWEeper-Bench/test.jsonl").read_text().splitlines())
                   if row["case_id"] == "sweeper-151")
        commit = row["base_commit" if self.build == "A" else "reference_commit"]
        self.app = self.root / "vendor/apps/pocketbase" / ("base" if self.build == "A" else "fixed")
        binary = native_go_binary("sweeper-151", commit, self.app, CACHE / "sweeper-151/rootfs")
        self.data = self.directory / "data"
        environment = {**os.environ, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
        command = [str(binary), "superuser", "upsert", "jordan.reed@basestack.test", "SoftLedger8!pine", "--dir=" + str(self.data)]
        seeded = subprocess.run(command, capture_output=True, text=True, env=environment, check=True)
        (self.directory / "seed-cli.log").write_text(seeded.stdout + seeded.stderr)
        self.handle = (self.directory / "server.log").open("w")
        self.process = subprocess.Popen([str(binary), "serve", "--http=127.0.0.1:" + str(self.port), "--dir=" + str(self.data)],
                                        stdout=self.handle, stderr=self.handle, env=environment, start_new_session=True)
        self._seed_collections()
        helper = CACHE / "sweeper-151/helpers/product-yarn"
        (self.directory / "instance.json").write_text(json.dumps({
            "case_id": "sweeper-151", "build": self.build, "commit": commit, "url": self.url,
            "binary": str(binary), "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "compilation": json.loads((binary.parent / "provenance.json").read_text()),
            "seed_source": str(helper), "seed_source_sha256": hashlib.sha256(helper.read_bytes()).hexdigest(),
            "pid": self.process.pid}, indent=2) + "\n")
        return self

    def _seed_collections(self):
        session = requests.Session()
        session.trust_env = False
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                response = session.get(self.url + "/api/health", timeout=1)
                if response.ok:
                    break
            except requests.ConnectionError:
                time.sleep(0.1)
        response = session.post(self.url + "/api/collections/_superusers/auth-with-password", json={
            "identity": "jordan.reed@basestack.test", "password": "SoftLedger8!pine"}, timeout=10)
        response.raise_for_status()
        session.headers["Authorization"] = response.json()["token"]
        collections = [
            {"name": "items", "type": "base", "fields": [
                {"name": "price", "type": "text", "required": False},
                {"name": "price_id", "type": "text", "required": False}],
             "indexes": ["CREATE UNIQUE INDEX `idx_items_price_id` ON `items` (`price_id`)"]},
            {"name": "skus", "type": "base", "fields": [
                {"name": "title", "type": "text", "required": False},
                {"name": "sku", "type": "text", "required": False}],
             "indexes": ["CREATE UNIQUE INDEX `idx_skus_sku` ON `skus` (`sku`)"]}]
        for collection in collections:
            response = session.post(self.url + "/api/collections", json=collection, timeout=10)
            response.raise_for_status()
        (self.directory / "seed-collections.json").write_text(json.dumps(collections, indent=2) + "\n")
        session.close()

    def close(self):
        if self.process is not None and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            self.process.wait(timeout=10)
        if self.handle is not None:
            self.handle.close()
