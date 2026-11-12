"""Prepare pinned SWEeper images and run them with a user-owned PRoot process."""

import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import tarfile
import time
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "runs/dataset26-v1/images"
NEW_CASES = (151, 4, 146, 55, 49, 102, 83, 20, 101, 25, 37, 1, 192, 45, 9, 27, 43, 87, 74)
ACCEPT = ",".join(("application/vnd.oci.image.index.v1+json",
                   "application/vnd.oci.image.manifest.v1+json",
                   "application/vnd.docker.distribution.manifest.v2+json"))


def prepare_image(case_id, extract=True):
    directory = CACHE / case_id
    directory.mkdir(parents=True, exist_ok=True)
    metadata = directory / "manifest.json"
    session = requests.Session()
    token = session.get("https://ghcr.io/token", params={
        "service": "ghcr.io", "scope": f"repository:evigbyen/{case_id}:pull"}, timeout=30)
    token.raise_for_status()
    session.headers.update({"Authorization": "Bearer " + token.json()["token"], "Accept": ACCEPT})
    repository = f"https://ghcr.io/v2/evigbyen/{case_id}"
    if metadata.exists():
        manifest = json.loads(metadata.read_text())
    else:
        response = session.get(repository + "/manifests/alpha", timeout=30)
        response.raise_for_status()
        index = response.json()
        descriptor = next(item for item in index["manifests"]
                          if item["platform"] == {"architecture": "amd64", "os": "linux"})
        response = session.get(repository + "/manifests/" + descriptor["digest"], timeout=30)
        response.raise_for_status()
        manifest = response.json()
        manifest["resolved_image_digest"] = descriptor["digest"]
        (directory / "index.json").write_text(json.dumps(index, indent=2) + "\n")
        metadata.write_text(json.dumps(manifest, indent=2) + "\n")
    config_path = directory / "config.json"
    if not config_path.exists():
        response = session.get(repository + "/blobs/" + manifest["config"]["digest"], timeout=30)
        response.raise_for_status()
        config_path.write_text(json.dumps(response.json(), indent=2) + "\n")
    config = json.loads(config_path.read_text())
    helpers = directory / "helpers"
    helpers.mkdir(exist_ok=True)
    for item in config["history"]:
        command = item.get("created_by", "")
        for match in re.finditer(r"cat > (/[^\s]+) <<'([^']+)'\n(.*?)\n\2(?:\s|$)", command, re.S):
            target = helpers / Path(match[1]).name
            target.write_text(match[3] + "\n")
            target.chmod(0o755)
    if not extract or (directory / "rootfs.complete.json").exists():
        return directory
    layers = manifest["layers"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(download_blob, repository, session.headers["Authorization"], item)
                   for item in layers]
        paths = [future.result() for future in futures]
    rootfs = directory / "rootfs"
    rootfs.mkdir(exist_ok=True)
    for item, path in zip(layers, paths):
        extract_layer(path, rootfs)
        print(json.dumps({"case_id": case_id, "extracted_layer": item["digest"]}), flush=True)
    (directory / "rootfs.complete.json").write_text(json.dumps({
        "image": f"ghcr.io/evigbyen/{case_id}:alpha", "digest": manifest["resolved_image_digest"],
        "layers": len(layers), "compressed_bytes": sum(item["size"] for item in layers)}, indent=2) + "\n")
    return directory


def download_blob(repository, authorization, descriptor):
    digest = descriptor["digest"].split(":", 1)[1]
    directory = CACHE / "blobs"
    directory.mkdir(exist_ok=True)
    destination = directory / (digest + ".tar.gz")
    if destination.exists():
        return destination
    temporary = destination.with_suffix(".partial")
    existing = temporary.stat().st_size if temporary.exists() else 0
    # Registry TLS needs the configured proxy; the proxy truncates blob bodies.
    # Resolve the signed Azure URL through it, then read that URL directly.
    redirect = requests.get(repository + "/blobs/" + descriptor["digest"],
                            headers={"Authorization": authorization}, allow_redirects=False, timeout=30)
    redirect.raise_for_status()
    location = redirect.headers["Location"]
    session = requests.Session()
    session.trust_env = False
    checksum = hashlib.sha256()
    if existing:
        with temporary.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                checksum.update(chunk)
    response = session.get(location, headers={"Range": f"bytes={existing}-{descriptor['size'] - 1}"},
                           stream=True, timeout=(30, 60))
    response.raise_for_status()
    if response.status_code != 206:
        raise ValueError("OCI blob did not honor its byte range")
    with temporary.open("ab") as handle:
        for chunk in response.iter_content(1024 * 1024):
            handle.write(chunk)
            checksum.update(chunk)
    if checksum.hexdigest() != digest:
        raise ValueError("OCI layer digest differs: " + descriptor["digest"])
    temporary.rename(destination)
    print(json.dumps({"downloaded": descriptor["digest"], "bytes": destination.stat().st_size}), flush=True)
    return destination


def extract_layer(path, destination):
    with tarfile.open(path) as archive:
        members = archive.getmembers()
        for member in members:
            name = Path(member.name)
            if name.name == ".wh..wh..opq":
                target = destination / name.parent
                if target.is_dir():
                    for child in target.iterdir():
                        remove_path(child)
            elif name.name.startswith(".wh."):
                remove_path(destination / name.parent / name.name[4:])
    subprocess.run(["tar", "--extract", "--gzip", "--file", str(path), "--directory", str(destination),
                    "--no-same-owner", "--no-same-permissions", "--anchored", "--exclude=dev/*",
                    "--exclude=proc/*", "--exclude=sys/*", "--exclude=*/.wh.*"], check=True)


def remove_path(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def native_go_binary(case_id, commit, source, rootfs):
    directory = CACHE / "native-binaries" / case_id / commit
    binary = directory / "application"
    if binary.exists():
        return binary
    directory.mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, "GOROOT": str(rootfs / "usr/local/go"),
                   "GOPATH": str(rootfs / "go"), "GOCACHE": str(CACHE / "native-go-cache"),
                   "CGO_ENABLED": "0", "GOTOOLCHAIN": "local", "GOPROXY": "off", "GOSUMDB": "off",
                   "GOFLAGS": "-buildvcs=false", "GOMAXPROCS": "8"}
    flags = "-s -w" if case_id == "sweeper-151" else (
        "-X github.com/apache/answer/cmd.Version=bench -X github.com/apache/answer/cmd.Revision=bench -extldflags -static")
    target = "./examples/base" if case_id == "sweeper-151" else "./cmd/answer"
    command = [str(rootfs / "usr/local/go/bin/go"), "build", "-p", "8", "-trimpath", "-ldflags", flags,
               "-o", str(binary), target]
    with (directory / "build.log").open("w") as handle:
        subprocess.run(command, cwd=source, env=environment, stdout=handle, stderr=handle, check=True)
    (directory / "provenance.json").write_text(json.dumps({
        "case_id": case_id, "commit": commit, "source": str(source), "command": command,
        "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}, indent=2) + "\n")
    return binary


class ImageInstance:
    def __init__(self, root, directory, case_id, build, port, env=None):
        self.root = Path(root).resolve()
        self.directory = Path(directory).resolve()
        self.case_id, self.build, self.port = case_id, build, port
        self.db_port = port + 10000
        self.url = f"http://127.0.0.1:{port}"
        self.image = CACHE / case_id
        self.rootfs = self.image / "rootfs"
        self.app = self.directory / "workspace"
        self.process = None
        self.handle = None
        self.extra_env = env or {}
        self.command_prefix = None

    def start(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        if not (self.image / "rootfs.complete.json").exists():
            raise RuntimeError("Image is not prepared: " + self.case_id)
        cases = {row["case_id"]: row for row in map(json.loads, (
            self.root / "vendor/SWEeper-Bench/test.jsonl").read_text().splitlines())}
        case = cases[self.case_id]
        commit = case["base_commit" if self.build == "A" else "reference_commit"]
        source = self.root / "vendor/apps" / case["slug"] / ("base" if self.build == "A" else "fixed")
        shutil.copytree(source, self.app, ignore=shutil.ignore_patterns(".git"), dirs_exist_ok=True)
        for name in ("vendor/.htaccess", "public_html/install"):
            if (source / name).exists():
                subprocess.run(["git", "init", "--quiet", str(self.app)], check=True)
                subprocess.run(["git", "add", "--force", name], cwd=self.app, check=True)
        bindings = [(self.app, "/workspace/product")]
        for name in ("tmp", "run", "var/lib", "var/log", "etc", "root"):
            target = self.directory / "writable" / name
            origin = self.rootfs / name
            if origin.is_dir():
                shutil.copytree(origin, target, dirs_exist_ok=True, symlinks=True)
            else:
                target.mkdir(parents=True, exist_ok=True)
            bindings.append((target, "/" + name))
        (self.directory / "writable/etc/hosts").write_text(
            "127.0.0.1 localhost " + socket.gethostname() + "\n::1 localhost\n")
        shutil.copy2("/etc/resolv.conf", self.directory / "writable/etc/resolv.conf")
        if self.case_id in {"sweeper-004", "sweeper-146", "sweeper-055", "sweeper-087", "sweeper-027"}:
            shutil.rmtree(self.directory / "writable/var/lib/mysql")
        proot = CACHE / "runtime/usr/bin/proot-new"
        command = [str(proot), "-0", "-r", str(self.rootfs), "-b", "/dev", "-b", "/proc",
                   "-b", "/sys", "-w", "/workspace/product", "-p", f"3306:{self.db_port}",
                   "-p", f"5432:{self.db_port + 1}", "-p", f"13200:{self.port}"]
        for host, guest in bindings:
            command += ["-b", str(host) + ":" + guest]
        router_name = {"sweeper-101": "linkace-router.php", "sweeper-074": "grocy-router.php"}.get(self.case_id)
        if router_name:
            router = self.directory / router_name
            router.write_text((self.rootfs / "opt" / router_name).read_text().replace("13200", str(self.port)))
            command += ["-b", str(router) + ":/opt/" + router_name]
        config = json.loads((self.image / "config.json").read_text())["config"]
        environment = dict(value.split("=", 1) for value in config.get("Env", []))
        environment = {key: value.replace("13200", str(self.port)) for key, value in environment.items()}
        environment.update(HOST="127.0.0.1", PORT=str(self.port), BROWSER="none", TERM="xterm",
                           HOME="/root", no_proxy="127.0.0.1,localhost,::1", NO_PROXY="127.0.0.1,localhost,::1")
        environment.update(self.extra_env)
        startup = (self.rootfs / "usr/local/bin/product-yarn").read_text().replace("13200", str(self.port))
        if self.case_id == "sweeper-004":
            startup = startup.split("\nwhile true; do", 1)[0] + (
                '\nexec php -S "${HOST}:${PORT}" -t "${APP_ROOT}" "${APP_ROOT}/router.php"\n')
        if self.case_id == "sweeper-045":
            driver = Path(__file__).with_name("dataset26_evershop_build.mjs")
            shutil.copy2(driver, self.app / ".dataset26-build-product-edit.mjs")
            build_cache = CACHE / "evershop-builds" / commit
            build_cache.mkdir(parents=True, exist_ok=True)
            command += ["-b", str(build_cache) + ":/dataset26-evershop-build-cache"]
            build_info = json.dumps({"commit": commit, "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest()})
            rebuild = ("if [ -f /dataset26-evershop-build-cache/complete.json ]; then\n"
                       "  rm -rf .evershop/build\n"
                       "  cp -a /dataset26-evershop-build-cache/build .evershop/build\n"
                       "else\n"
                       "  node .dataset26-build-product-edit.mjs >/tmp/evershop-product-edit-build.log 2>&1\n"
                       "  cp -a .evershop/build /dataset26-evershop-build-cache/build\n"
                       f"  printf '%s\\n' '{build_info}' >/dataset26-evershop-build-cache/complete.json\n"
                       "fi\n")
            startup = startup.replace("test -d .evershop/build\n", rebuild + "test -d .evershop/build\n")
            startup = startup.replace('wait "${SERVER_PID}"', 'touch /tmp/dataset26-ready\nwait "${SERVER_PID}"')
        entrypoint = self.rootfs / "usr/local/bin/product-container-entrypoint"
        if entrypoint.exists():
            initialize = entrypoint.read_text().split("exec sleep infinity", 1)[0]
        else:
            initialize = ""
        if self.case_id == "sweeper-055":
            initialize += ("\nmysql_install_db --user=mysql --datadir=/var/lib/mysql "
                           "--auth-root-authentication-method=normal >/tmp/dataset26-mysql-install.log 2>&1\n")
        script = self.directory / "startup.sh"
        script.write_text("#!/bin/bash\nset -euo pipefail\n" + initialize
                          + "\nexec /bin/bash /dataset26-product-yarn start\n")
        helper = self.directory / "product-yarn"
        helper.write_text(startup)
        command += ["-b", str(script) + ":/dataset26-startup.sh",
                    "-b", str(helper) + ":/dataset26-product-yarn"]
        self.command_prefix = command + ["/usr/bin/env", *[key + "=" + value for key, value in environment.items()]]
        command = self.command_prefix + ["/bin/bash", "/dataset26-startup.sh"]
        self.handle = (self.directory / "server.log").open("w")
        self.process = subprocess.Popen(command, stdout=self.handle, stderr=self.handle, start_new_session=True)
        (self.directory / "instance.json").write_text(json.dumps({
            "case_id": self.case_id, "build": self.build, "commit": commit, "url": self.url,
            "image_digest": json.loads((self.image / "manifest.json").read_text())["resolved_image_digest"],
            "source": str(source), "pid": self.process.pid,
            "proot_sha256": hashlib.sha256(proot.read_bytes()).hexdigest(),
            "startup_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
            "helper_sha256": hashlib.sha256(helper.read_bytes()).hexdigest()}, indent=2) + "\n")
        session = requests.Session()
        session.trust_env = False
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"{self.case_id} startup exited; see {self.directory}/server.log")
            try:
                response = session.get(self.url, timeout=2)
                if response.status_code < 500 and (self.case_id != "sweeper-045" or (
                        self.directory / "writable/tmp/dataset26-ready").exists()):
                    return self
            except requests.RequestException:
                pass
            time.sleep(0.5)
        raise RuntimeError(f"{self.case_id} startup timed out; see {self.directory}/server.log")

    def close(self):
        if self.case_id == "sweeper-045" and (
                self.directory / "writable/tmp/evershop-pgdata/postmaster.pid").exists():
            stopped = self.run(["/bin/bash", "-lc", "gosu postgres /usr/lib/postgresql/*/bin/pg_ctl "
                                "-D /tmp/evershop-pgdata -m fast stop"])
            (self.directory / "postgres-stop.log").write_text(stopped.stdout + stopped.stderr)
        if self.process is not None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait()
        if self.handle is not None:
            self.handle.close()
        for root in (self.directory / "writable/tmp", self.app / "storage/logs"):
            if root.exists():
                for path in root.rglob("*.log"):
                    destination = self.directory / "application-logs" / path.relative_to(root)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, destination)
        for path in (self.app, self.directory / "writable"):
            if path.exists():
                shutil.rmtree(path)

    def run(self, arguments, **kwargs):
        return subprocess.run(self.command_prefix + list(arguments), check=True, text=True,
                              capture_output=True, **kwargs)
