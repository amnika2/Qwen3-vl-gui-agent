import copy
import hashlib
import json
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

from .native import NativeInstance


class PluxmlInstance(NativeInstance):
    case_id = "sweeper-150"
    slug = "pluxml"
    web_root = "."
    health_path = "/install.php"

    def configure(self):
        checkout = self.root / "vendor/apps/pluxml" / ("base" if self.build == "A" else "fixed")
        shutil.copy2(checkout / "config.php", self.app / "config.php")
        (self.app / "data/configuration").mkdir(exist_ok=True)
        ini = self.socket_dir / "php-ini"
        ini.mkdir()
        # Match the normal web PHP buffer; CLI's unbuffered warning breaks the redirect before the login check.
        (ini / "pluxml.ini").write_text("output_buffering=4096\n")
        self.env["PHP_INI_SCAN_DIR"] = str(ini)

    def _seed(self):
        fixture = json.loads((self.root / "cases/sweeper-150/fixtures.private.json").read_text())["preservation"]
        response = self.http.get(self.url + "/install.php", timeout=10)
        response.raise_for_status()
        token = re.search(r'name="token" value="([^"]+)"', response.text)[1]
        response = self.http.post(self.url + "/install.php", data={"token": token, "default_lang": "en", "data": "1",
            "name": "Elena Voss", "login": fixture["username"], "password1": fixture["password"],
            "password2": fixture["password"], "email": "qa@guiqa.test", "timezone": "UTC", "install": "Install"}, timeout=15)
        response.raise_for_status()
        path = self.app / "data/configuration/users.xml"
        tree = ET.parse(path)
        user = copy.deepcopy(tree.getroot().find("user"))
        user.set("number", "002")
        user.set("profil", "3")
        salt = "guiqaseed2"
        password = hashlib.sha1((salt + hashlib.md5(b"priorCreek7!").hexdigest()).encode()).hexdigest()
        for key, value in {"login": "jonah19", "name": "Jonah Nineteen", "salt": salt, "password": password,
                           "email": "jonah@guiqa.test"}.items():
            user.find(key).text = value
        tree.getroot().append(user)
        tree.write(path, encoding="utf-8", xml_declaration=True)
        (self.app / "install.php").unlink()


class DokuwikiInstance(NativeInstance):
    case_id = "sweeper-034"
    slug = "dokuwiki"
    web_root = "."
    health_path = "/install.php"

    def configure(self):
        # The base start() copies the source tree but skips the top-level vendor/ dir.
        checkout = self.root / "vendor/apps/dokuwiki" / ("base" if self.build == "A" else "fixed")
        shutil.copytree(checkout / "vendor", self.app / "vendor")

    def _seed(self):
        fixture = json.loads((self.root / "cases/sweeper-034/fixtures.private.json").read_text())["target"]
        response = self.http.get(self.url + "/install.php", timeout=10)
        response.raise_for_status()
        response = self.http.post(self.url + "/install.php", data={
            "l": "en", "d[title]": "GUIQA Wiki", "d[acl]": "1",
            "d[superuser]": fixture["username"], "d[fullname]": "GUIQA Admin",
            "d[email]": "admin@guiqa.test", "d[password]": fixture["password"],
            "d[confirm]": fixture["password"], "d[policy]": "1", "d[pop]": "0",
            "submit": "Save"}, timeout=20)
        response.raise_for_status()
        if not (self.app / "conf/local.php").exists():
            (self.directory / "install-failure.html").write_text(response.text)
            raise RuntimeError("dokuwiki installer did not produce conf/local.php")
        (self.app / "install.php").unlink()

    def seed_training_page(self, fixture):
        path = self.app / "data/pages" / (fixture["page_id"].replace(":", "/") + ".txt")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fixture["initial"])


class FlatpressInstance(NativeInstance):
    case_id = "sweeper-053"
    slug = "flatpress"
    web_root = "."
    # /setup.php is stateful (it advances the installer step), so health-check a static file instead.
    health_path = "/README.md"

    def configure(self):
        pass

    def _seed(self):
        fixture = json.loads((self.root / "cases/sweeper-053/fixtures.private.json").read_text())["target"]
        response = self.http.get(self.url + "/setup.php", timeout=20)
        response.raise_for_status()
        match = re.search(r'name="setupid" value="([^"]+)"', response.text)
        if not match:
            (self.directory / "install-failure.html").write_text(response.text)
            raise RuntimeError("flatpress setup did not render setupid on the first step")
        setupid = match[1]
        response = self.http.post(self.url + "/setup.php",
            data={"setupid": setupid, "start": "Next"}, timeout=20)
        response.raise_for_status()
        setupid = re.search(r'name="setupid" value="([^"]+)"', response.text)[1]
        response = self.http.post(self.url + "/setup.php", data={
            "setupid": setupid, "language": "en-us",
            "fpuser": fixture["username"], "fppwd": fixture["password"],
            "fppwd2": fixture["password"], "www": self.url + "/",
            "email": "admin@guiqa.test", "start": "Next"}, timeout=20)
        response.raise_for_status()
        if not (self.app / "fp-content" / "%%setup.lock").exists():
            (self.directory / "install-failure.html").write_text(response.text)
            raise RuntimeError("flatpress installer did not produce fp-content/%%setup.lock")

    def seed_training_page(self, fixture):
        program = ("require 'defaults.php'; require INCLUDES_DIR.'includes.php';"
                   "$s=json_decode(stream_get_contents(STDIN),true);"
                   "$entry=['id'=>$s['page_id'],'subject'=>$s['initial_subject'],'content'=>$s['initial'],"
                   "'author'=>'admin','date'=>1700000000,'version'=>system_ver()];"
                   "exit(static_save($entry,$s['page_id']) ? 0 : 1);")
        with (self.directory / "scenario-seed.log").open("w") as handle:
            subprocess.run([str(self.runtime / "php"), "-r", program], cwd=self.app, env=self.env,
                           input=json.dumps(fixture), text=True, check=True, stdout=handle, stderr=subprocess.STDOUT)


class WondercmsInstance(NativeInstance):
    case_id = "sweeper-196"
    slug = "wondercms"
    web_root = "."
    health_path = "/"
    router = "wondercms-router.php"

    def configure(self):
        # WonderCMS relies on .htaccess rewriting (e.g. /loginURL -> index.php?page=loginURL);
        # PHP's built-in server does not read .htaccess, so emulate that single rule here.
        (self.app / self.router).write_text(
            "<?php\n"
            "$path = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH) ?: '/';\n"
            "if ($path !== '/' && !is_file(__DIR__ . $path)) {\n"
            "    $_GET['page'] = ltrim($path, '/');\n"
            "    $_REQUEST['page'] = $_GET['page'];\n"
            "    return include __DIR__ . '/index.php';\n"
            "}\n"
            "return false;\n")

    def _seed(self):
        fixture = json.loads((self.root / "cases/sweeper-196/fixtures.private.json").read_text())["target"]
        response = self.http.get(self.url + "/", timeout=15)
        response.raise_for_status()
        db_path = self.app / "data" / "database.js"
        if not db_path.exists():
            (self.directory / "install-failure.html").write_text(response.text)
            raise RuntimeError("wondercms first load did not create data/database.js")
        db = json.loads(db_path.read_text())
        hashed = subprocess.check_output(
            [str(self.runtime / "php"), "-r",
             "echo password_hash(stream_get_contents(STDIN), PASSWORD_DEFAULT);"],
            input=fixture["password"], text=True, env=self.env)
        db["config"]["password"] = hashed
        db_path.write_text(json.dumps(db, ensure_ascii=False, indent=2) + "\n")


class TypesetterInstance(NativeInstance):
    case_id = "sweeper-185"
    slug = "typesetter"
    web_root = "."
    health_path = "/index.php/"
    # The upstream error handler requires PHP 7's fifth error-context argument.
    php_name = "php74/php"
    runtime_description = "PHP 7.4.3 / MariaDB 10.11.14, user-owned processes"

    def configure(self):
        pass

    def _seed(self):
        self._write_theme()
        self._install()
        self._set_theme()

    def _write_theme(self):
        theme = self.app / "themes" / "Shoremark"
        (theme / "Default").mkdir(parents=True)
        (theme / "Addon.ini").write_text("Addon_Name = 'Shoremark'\nAddon_Version = 1.0\n")
        (theme / "settings.php").write_text(
            "<?php\n"
            "$link = common::Link('', '%s');\n"
            "gpOutput::Area('header', '<h1>' . $link . '</h1>');\n"
            "gpOutput::Area('side_note', '<h3>%s</h3>');\n")
        (theme / "template.php").write_text(
            "<!DOCTYPE html>\n"
            "<html>\n<head>\n<meta charset=\"UTF-8\" />\n"
            "<?php gpOutput::GetHead(); ?>\n"
            "</head>\n<body>\n<div id=\"wrapper\">\n"
            "<div id=\"logo\">\n"
            "<?php global $config; gpOutput::GetArea('header', $config['title']); ?>\n"
            "<a id=\"edit-header\" href=\"<?php echo \\gp\\tool::GetUrl('Admin_Theme_Content/Text', "
            "'cmd=EditText&key=' . urlencode($config['title'])); ?>\">Edit header</a>\n"
            "</div>\n"
            "<div class=\"side-note-area\">\n"
            "<?php gpOutput::GetArea('side_note', 'SideNote'); ?>\n"
            "<a id=\"edit-sidenote\" href=\"<?php echo \\gp\\tool::GetUrl('Admin_Theme_Content/Text', "
            "'cmd=EditText&key=SideNote'); ?>\">Edit side note</a>\n"
            "</div>\n"
            "<div id=\"content\">\n<?php $page->GetContent(); ?>\n</div>\n"
            "<div id=\"footer\">\n<?php gpOutput::GetAdminLink(); ?>\n</div>\n"
            "</div>\n</body>\n</html>\n")
        (theme / "Default" / "style.css").write_text(
            "#wrapper { margin: 60px 40px 40px 190px; }\n")

    def _install(self):
        fixture = json.loads((self.root / "cases/sweeper-185/fixtures.private.json").read_text())["target"]
        response = self.http.post(self.url + "/index.php/", data={
            "cmd": "Install", "site_title": "Shoremark", "email": "admin@guiqa.test",
            "username": fixture["username"], "password": fixture["password"],
            "password1": fixture["password"]}, timeout=30)
        response.raise_for_status()
        if not (self.app / "data/_site/config.php").exists():
            (self.directory / "install-failure.html").write_text(response.text)
            raise RuntimeError("typesetter installer did not produce data/_site/config.php")

    def _set_theme(self):
        pages = self.app / "data/_site/pages.php"
        pages.write_text(pages.read_text().replace("Bootswatch_Scss/Flatly", "Shoremark/Default"))
