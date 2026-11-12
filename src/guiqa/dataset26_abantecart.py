"""Run AbanteCart using its bundled dependencies and the existing PHP runtime."""

import hashlib

from .dataset26_runtime import write_json
from .native import NativeInstance


class AbantecartInstance(NativeInstance):
    case_id = "sweeper-001"
    slug = "abantecart"
    web_root = "public_html"
    health_path = "/index.php?rt=account/login"
    router = "dataset26-router.php"

    def configure(self):
        helpers = self.root / "runs/dataset26-v1/images/sweeper-001/helpers"
        self.env.update(AC_PUBLIC_HOST="127.0.0.1", AC_PUBLIC_PORT=str(self.port))
        for name, target in (("ac-router.php", self.router), ("ac-seed.php", "dataset26-seed.php")):
            text = (helpers / name).read_text().replace("/workspace/product/public_html", str(self.app / self.web_root))
            (self.app / target).write_text(text)
        public = self.app / self.web_root
        (public / "system/config.php").unlink(missing_ok=True)
        for name in ("system/cache", "system/logs", "image", "download", "resources", "admin/system/uploads/customerfiles"):
            (public / name).mkdir(parents=True, exist_ok=True)
        self._command([str(self.runtime / "php"), "-d", f"mysqli.default_socket={self.socket}",
            "install/cli_install.php", "install", "--db_host=localhost", "--db_user=root", "--db_password=",
            "--db_name=guiqa", "--db_driver=amysqli", "--db_prefix=ac_", "--admin_path=admin",
            "--username=dataset26admin", "--password=Dataset26Admin!", "--email=admin@dataset26.test",
            "--http_server=" + self.url + "/", "--template=default",
            "--with-sample-data=abantecart_sample_data.sql"], "app-install.log", cwd=public)
        write_json(self.directory / "helper-source.json", {name: hashlib.sha256((helpers / name).read_bytes()).hexdigest()
                   for name in ("ac-router.php", "ac-seed.php")})

    def _seed(self):
        self._command([str(self.runtime / "php"), "-d", f"mysqli.default_socket={self.socket}",
                       str(self.app / "dataset26-seed.php")], "seed.log", cwd=self.app)
