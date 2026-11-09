import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import requests

from .artifacts import write_json

class NativeInstance:
    router = None
    php_name = "php"
    runtime_description = "PHP 8.3.6 / MariaDB 10.11.14, user-owned processes"
    def __init__(self, root: Path, directory: Path, build: str, port: int):
        self.root = root.resolve()
        self.directory = directory.resolve()
        self.build = build
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self.runtime = self.root / "vendor/runtime"
        self.processes = []
        self.handles = []
        self.socket_dir = Path(tempfile.mkdtemp(prefix="guiqa-"))
        self.socket = self.socket_dir / "db.sock"
        self.app = self.socket_dir / "app"
        self.env = {**os.environ, "LD_LIBRARY_PATH": str(self.runtime / "root/usr/lib/x86_64-linux-gnu"),
                    "TMPDIR": str(self.socket_dir)}
        self.http = requests.Session()
        self.http.trust_env = False

    def start(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        private = json.loads((self.root / "cases" / self.case_id / "private.json").read_text())
        commit = private["base_commit" if self.build == "A" else "reference_commit"]
        checkout = self.root / "vendor/apps" / self.slug / ("base" if self.build == "A" else "fixed")
        # Tarball-based checkouts (no .git) are trusted from the download URL; git
        # worktrees are verified against the expected commit.
        actual = commit
        if (checkout / ".git").exists():
            actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
        if actual != commit:
            raise RuntimeError(f"checkout mismatch: expected {commit}, found {actual}")
        shutil.copytree(checkout, self.app, ignore=lambda path, names:
            set(names) & {".git", "config.php", ".env", "vendor", "node_modules"} if Path(path) == checkout else set())
        data = self.socket_dir / "database"
        self._command([str(self.runtime / "root/usr/bin/mariadb-install-db"), "--no-defaults",
                       f"--basedir={self.runtime}/root/usr", f"--datadir={data}",
                       f"--tmpdir={self.socket_dir}",
                       "--auth-root-authentication-method=normal"], "database-init.log")
        self._spawn([str(self.runtime / "root/usr/sbin/mariadbd"), "--no-defaults",
                     f"--basedir={self.runtime}/root/usr", f"--datadir={data}",
                     f"--socket={self.socket}", f"--pid-file={self.socket_dir}/db.pid",
                     f"--tmpdir={self.socket_dir}",
                     "--skip-networking", "--innodb-buffer-pool-size=32M", "--max-connections=10"], "database.log")
        self._wait_database()
        self.sql("CREATE DATABASE guiqa CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
        self.configure()
        sessions = self.socket_dir / "sessions"
        sessions.mkdir()
        server = [str(self.runtime / self.php_name), "-d", f"mysqli.default_socket={self.socket}",
                    "-d", f"session.save_path={sessions}", "-S", f"127.0.0.1:{self.port}",
                    "-t", str(self.app / self.web_root)]
        if self.router:
            server.append(str(self.app / self.router))
        self._spawn(server, "http.log", cwd=self.app)
        self.health()
        self._seed()
        clean = True
        if (checkout / ".git").exists():
            clean = not bool(subprocess.check_output(
                ["git", "status", "--porcelain", "--untracked-files=no"], cwd=checkout))
        write_json(self.directory / "instance.json", {"case_id": self.case_id, "build": self.build,
                   "commit": commit, "url": self.url, "database_socket": str(self.socket),
                   "source_tree_clean": clean,
                   "runtime": self.runtime_description})
        return self

    def close(self):
        for process in reversed(self.processes):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for handle in self.handles:
            handle.close()
        logs = self.app / "storage/logs"
        if logs.exists():
            shutil.copytree(logs, self.directory / "application-logs", dirs_exist_ok=True)
        self.http.close()
        shutil.rmtree(self.socket_dir)

    def health(self):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.processes[-1].poll() is not None:
                raise RuntimeError(f"HTTP process exited; see {self.directory}/http.log")
            try:
                response = self.http.get(self.url + self.health_path, timeout=2)
                if response.status_code == 200:
                    return
            except (requests.ConnectionError, requests.Timeout):
                pass
            time.sleep(0.1)
        raise RuntimeError(f"application did not become healthy; see {self.directory}/http.log")

    def sql(self, text):
        return subprocess.check_output([str(self.runtime / "root/usr/bin/mariadb"), "--no-defaults",
                 f"--socket={self.socket}", "-u", "root", "--batch", "--skip-column-names"],
                 input=text, text=True, env=self.env)

    def _command(self, args, log, cwd=None):
        with (self.directory / log).open("w") as handle:
            result = subprocess.run(args, env=self.env, stdout=handle, stderr=handle, cwd=cwd)
        if result.returncode:
            raise RuntimeError(f"{log} exited {result.returncode}; inspect {self.directory / log}")

    def _spawn(self, args, log, cwd=None):
        handle = (self.directory / log).open("w")
        self.handles.append(handle)
        self.processes.append(subprocess.Popen(args, cwd=cwd, env=self.env, stdout=handle, stderr=handle))

    def _wait_database(self):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.socket.exists():
                try:
                    self.sql("SELECT 1;")
                    return
                except subprocess.CalledProcessError:
                    pass
            time.sleep(0.1)
        raise RuntimeError(f"database did not become ready; see {self.directory}/database.log")


class EmlogInstance(NativeInstance):
    case_id = "sweeper-041"
    slug = "emlog"
    web_root = "."
    health_path = "/install.php"

    def configure(self):
        (self.app / "config.php").touch()

    def _seed(self):
        fixture = json.loads((self.root / "cases/sweeper-041/fixtures.private.json").read_text())
        admin = fixture["preservation"]
        response = self.http.post(self.url + "/install.php?action=install", data={
            "hostname": "localhost", "dbuser": "root", "dbpasswd": "", "dbname": "guiqa",
            "dbprefix": "emlog_", "username": admin["username"], "password": admin["password"],
            "repassword": admin["password"], "email": "admin@guiqa.test"}, timeout=20)
        response.raise_for_status()
        if not (self.app / "config.php").stat().st_size:
            (self.directory / "install-failure.html").write_text(response.text)
            raise RuntimeError("emlog installer did not produce config.php")
        # Both versions use identical English rendering and synthetic fixtures.
        config = self.app / "config.php"
        config.write_text(config.read_text().replace("EMLOG_LANG = 'zh_CN'", "EMLOG_LANG = 'en_US'"))
        target = fixture["target"]
        program = (f"require {json.dumps(str(self.app / 'include/lib/passwordhash.php'))};"
                   "$h=new PasswordHash(8,true);echo $h->HashPassword(stream_get_contents(STDIN));")
        hashed = subprocess.check_output([str(self.runtime / "php"), "-r", program],
                                        input=target["password"], text=True, env=self.env)
        self.sql(f"USE guiqa; INSERT INTO emlog_user (uid,username,password,nickname,role,email,create_time,update_time) "
                 f"VALUES (2,'{target['username']}','{hashed}','Original Pen Name','writer','writer@guiqa.test',1,1);"
                 "UPDATE emlog_options SET option_value='n' WHERE option_name='login_code';")



class AkauntingInstance(NativeInstance):
    case_id = "sweeper-005"
    slug = "akaunting"
    web_root = "."
    health_path = "/auth/login"
    router = "akaunting-router.php"

    def configure(self):
        prepared = self.root / "vendor/apps/akaunting/base"
        # The upstream fixture blocks the public API in /etc/hosts. Apply its offline policy to this process only.
        for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            self.env[key] = "http://127.0.0.1:9"
        self.env["NO_PROXY"] = self.env["no_proxy"] = "127.0.0.1,localhost"
        for name in ("akaunting-router.php", "akaunting-seed-caches.php"):
            shutil.copy2(self.root / "vendor/akaunting-image/helpers/opt" / name, self.app / name)
        router = self.app / self.router
        router.write_text(router.read_text().replace("13200", str(self.port)))
        # Neither dependency locks nor frontend sources differ between the two pinned commits.
        shutil.copytree(prepared / "vendor", self.app / "vendor")
        shutil.copytree(prepared / "modules", self.app / "modules", dirs_exist_ok=True)
        for name in ("js", "css", "mix-manifest.json"):
            source = prepared / "public" / name
            target = self.app / "public" / name
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True)
            else:
                shutil.copy2(source, target)
        fixture = json.loads((self.root / "cases/sweeper-005/fixtures.private.json").read_text())["target"]
        self.fixture = fixture
        env = (self.app / ".env.example").read_text()
        values = {"APP_URL": self.url, "APP_KEY": "base64:" + base64.b64encode(os.urandom(32)).decode(),
                  "APP_DEBUG": "false", "DB_DATABASE": "guiqa", "DB_USERNAME": "root", "DB_PREFIX": "ak_"}
        for key, value in values.items():
            env = re.sub(rf"(?m)^{key}=.*$", f"{key}={value}", env)
        (self.app / ".env").write_text(env + f"\nDB_SOCKET={self.socket}\n")
        self._command([str(self.runtime / "php"), "artisan", "package:discover"], "package-discover.log", cwd=self.app)
        self._command([str(self.runtime / "php"), "-d", f"pdo_mysql.default_socket={self.socket}",
            "artisan", "install", "--no-interaction", "--db-host=localhost",
            "--db-name=guiqa", "--db-username=root", "--db-prefix=ak_", "--company-name=GUIQA",
            "--company-email=company@guiqa.test", f"--admin-email={fixture['username']}",
            f"--admin-password={fixture['password']}", "--locale=en-GB"], "app-install.log", cwd=self.app)

    def _seed(self):
        self.sql("USE guiqa; UPDATE ak_settings SET value='1' WHERE `key`='wizard.completed';")
        if self.sql("USE guiqa; SELECT COUNT(*) FROM ak_items;").strip() != "0":
            raise RuntimeError("Akaunting fixture must start with an empty item catalog")
        env = self.app / ".env"
        env.write_text(env.read_text().replace("FIREWALL_ENABLED=true", "FIREWALL_ENABLED=false"))
        self._command([str(self.runtime / "php"), "akaunting-seed-caches.php"], "cache-fixture.log", cwd=self.app)


class AppointmentsInstance(NativeInstance):
    case_id = "sweeper-040"
    slug = "easyappointments"
    web_root = "."
    health_path = "/index.php/login"

    def configure(self):
        prepared = self.root / "vendor/apps/easyappointments/base"
        shutil.copytree(prepared / "vendor", self.app / "vendor")
        shutil.copytree(prepared / "assets", self.app / "assets", dirs_exist_ok=True)
        config = (self.app / "config-sample.php").read_text()
        values = {"BASE_URL": self.url, "DB_HOST": "localhost", "DB_NAME": "guiqa", "DB_USERNAME": "root", "DB_PASSWORD": ""}
        for key, value in values.items():
            config = re.sub(rf"const {key} = '.*?';", f"const {key} = '{value}';", config)
        (self.app / "config.php").write_text(config)
        self._command([str(self.runtime / "php"), "-d", f"mysqli.default_socket={self.socket}",
                       "index.php", "console", "install"], "app-install.log", cwd=self.app)
        fixture = json.loads((self.root / "cases/sweeper-040/fixtures.private.json").read_text())["target"]
        hashed = subprocess.check_output([str(self.runtime / "php"), "-r",
            'echo password_hash(stream_get_contents(STDIN),PASSWORD_BCRYPT,["cost"=>12]);'],
            input=fixture["password"], text=True, env=self.env)
        self.sql(f"USE guiqa; UPDATE ea_user_settings SET username='{fixture['username']}', password='{hashed}' "
                 "WHERE id_users=(SELECT id FROM ea_users WHERE email='john@example.org');"
                 "UPDATE ea_users SET email='jane@pippins.test',mobile_number='' WHERE email='jane@example.org';")

    def _seed(self):
        pass
