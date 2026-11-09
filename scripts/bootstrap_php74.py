import hashlib
import json
import subprocess
from pathlib import Path

import requests


def main():
    project = Path(__file__).resolve().parents[1]
    runtime = project / "vendor/runtime/php74"
    packages = {
        "php7.4-cli_7.4.3-4ubuntu2.29_amd64.deb": "main/p/php7.4",
        "php7.4-common_7.4.3-4ubuntu2.29_amd64.deb": "main/p/php7.4",
        "php7.4-mysql_7.4.3-4ubuntu2.29_amd64.deb": "main/p/php7.4",
        "php7.4-json_7.4.3-4ubuntu2.29_amd64.deb": "main/p/php7.4",
        "php7.4-mbstring_7.4.3-4ubuntu2.29_amd64.deb": "universe/p/php7.4",
        "libssl1.1_1.1.1f-1ubuntu2.24_amd64.deb": "main/o/openssl",
    }
    (runtime / "debs").mkdir(parents=True, exist_ok=True)
    records = []
    for name, folder in packages.items():
        url = f"https://archive.ubuntu.com/ubuntu/pool/{folder}/{name}"
        path = runtime / "debs" / name
        if not path.exists():
            response = requests.get(url, timeout=90)
            response.raise_for_status()
            path.write_bytes(response.content)
        records.append({"package": name, "url": url, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        subprocess.run(["dpkg-deb", "-x", str(path), str(runtime / "root")], check=True)
    extensions = ["ctype", "json", "mbstring", "mysqlnd", "pdo", "pdo_mysql", "phar", "tokenizer"]
    (runtime / "php.ini").write_text(f"extension_dir={runtime}/root/usr/lib/php/20190902\n" +
                                   "".join(f"extension={name}\n" for name in extensions) + "date.timezone=UTC\n")
    wrapper = runtime / "php"
    wrapper.write_text('''#!/bin/sh
R="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export LD_LIBRARY_PATH="$R/root/usr/lib/x86_64-linux-gnu:$R/../root/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$R/root/usr/bin/php7.4" -c "$R/php.ini" "$@"
''')
    wrapper.chmod(0o755)
    (project / "docs/php74-manifest.json").write_text(json.dumps(records, indent=2) + "\n")
    subprocess.run([str(wrapper), "-v"], check=True)


if __name__ == "__main__":
    main()
