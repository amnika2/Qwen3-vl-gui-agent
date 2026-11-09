import hashlib
import json
import re
import shutil
import subprocess
import uuid

from .native import NativeInstance


class AmpacheInstance(NativeInstance):
    case_id = "sweeper-007"
    slug = "ampache"
    web_root = "public"
    health_path = "/login.php"

    def configure(self):
        prepared = self.root / "vendor/apps/ampache/base"
        shutil.copytree(prepared / "vendor", self.app / "vendor")
        shutil.copytree(prepared / "public/lib", self.app / "public/lib", dirs_exist_ok=True)
        shutil.copytree(prepared / "public/dist", self.app / "public/dist", dirs_exist_ok=True)
        self.sql("USE guiqa;\n" + (self.app / "resources/sql/ampache.sql").read_text())
        config = (self.app / "config/ampache.cfg.php.dist").read_text()
        for key, value in {"database_hostname": str(self.socket), "database_name": "guiqa",
                           "database_username": "root", "database_password": "",
                           "secret_key": uuid.uuid4().hex}.items():
            config = re.sub(rf'(?m)^{key} = .*$', f'{key} = "{value}"', config)
        config = re.sub(r'(?m)^access_control = .*$', 'access_control = "false"', config)
        (self.app / "config/ampache.cfg.php").write_text(config)
        fixture = json.loads((self.root / "cases/sweeper-007/fixtures.private.json").read_text())["target"]
        username, password = fixture["username"], fixture["password"]
        self.fixture = {"username": username, "password": password}
        self.sql(f"USE guiqa; INSERT INTO `user` (id,username,password,access,fullname,email) "
                 f"VALUES(1,'{username}','{hashlib.sha256(password.encode()).hexdigest()}',100,'QA User','qa@guiqa.test');"
                 f"INSERT INTO user_preference (user,preference,name,value) SELECT 1,preference,name,value FROM user_preference WHERE user=-1;"
                 f"INSERT INTO search (name,type,user,username,rules,logic_operator,random,`limit`,date,last_update) "
                 f"VALUES ('Rename Me Smart','private',1,'{username}','[]','and',0,0,1,1);")

    def _seed(self):
        pass



class AnchorInstance(NativeInstance):
    case_id = "sweeper-008"
    slug = "anchor-cms"
    web_root = "."
    health_path = "/index.php/admin/login"
    php_name = "php74/php"
    runtime_description = "PHP 7.4.3 / MariaDB 10.11.14, user-owned processes"

    def configure(self):
        shutil.copytree(self.root / "vendor/anchor-deps/vendor", self.app / "vendor")
        schema = (self.app / "install/storage/anchor.sql").read_text()
        self.sql("USE guiqa;" + schema.replace("{{prefix}}", "anchor_").replace("{{charset}}", "utf8mb4").replace("{{now}}", "2026-11-07 00:00:00"))
        settings = {"database": {"prefix": "anchor_", "hostname": "localhost", "port": "3306", "username": "root", "password": "", "database": "guiqa"},
                    "application": {"url": self.url + "/", "index": "index.php", "timezone": "UTC", "key": uuid.uuid4().hex, "language": "en_GB"}}
        for name, values in settings.items():
            template = (self.app / f"install/storage/{name}.distro.php").read_text()
            for key, value in values.items():
                template = template.replace("{{" + key + "}}", value)
            destination = "app" if name == "application" else name
            (self.app / f"anchor/config/{destination}.php").write_text(template)
        shutil.copy2(self.app / "install/storage/session.distro.php", self.app / "anchor/config/session.php")
        ini = self.socket_dir / "ini"
        ini.mkdir()
        compatibility = self.socket_dir / "legacy-php.php"
        # The legacy shutdown handler treats PHP 7.4's harmless boot deprecation as a fatal error.
        compatibility.write_text("<?php set_error_handler(function () { return true; }, E_DEPRECATED);\n")
        (ini / "socket.ini").write_text(f"pdo_mysql.default_socket={self.socket}\n"
                                       f"auto_prepend_file={compatibility}\nallow_url_fopen=Off\n")
        self.env["PHP_INI_SCAN_DIR"] = str(ini)
        fixture = json.loads((self.root / "cases/sweeper-008/fixtures.private.json").read_text())["target"]
        hashed = subprocess.check_output([str(self.runtime / "php"), "-r", 'echo password_hash(stream_get_contents(STDIN),PASSWORD_BCRYPT);'],
                                        input=fixture["password"], text=True, env=self.env)
        self.sql(f"USE guiqa; INSERT INTO anchor_users (username,password,email,real_name,bio,status,role) "
                 f"VALUES ('{fixture['username']}','{hashed}','qa@guiqa.test','QA User','','active','administrator');"
                 "INSERT INTO anchor_meta (`key`,`value`) VALUES ('sitename','GUIQA'),('description','Test'),('theme','default'),('custom_greeting','Hello Anchor');")

    def _seed(self):
        pass

