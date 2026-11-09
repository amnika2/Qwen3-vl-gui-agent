from pathlib import Path


def main():
    runtime = Path(__file__).resolve().parents[1] / "vendor/runtime"
    modules = ["bcmath", "ctype", "curl", "dom", "fileinfo", "gd", "gettext", "iconv", "intl",
               "mbstring", "mysqlnd", "mysqli", "pdo", "pdo_mysql", "phar", "simplexml",
               "sockets", "tokenizer", "xml", "xmlreader", "xmlwriter", "zip"]
    (runtime / "sessions").mkdir(exist_ok=True)
    (runtime / "php.ini").write_text(
        f"extension_dir={runtime}/root/usr/lib/php/20230831\n"
        + "".join(f"extension={module}\n" for module in modules)
        + f"date.timezone=UTC\nsession.save_path={runtime}/sessions\n"
    )
    wrapper = runtime / "php"
    wrapper.write_text('''#!/bin/sh
R="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export LD_LIBRARY_PATH="$R/root/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$R/root/usr/bin/php8.3" -c "$R/php.ini" "$@"
''')
    wrapper.chmod(0o755)


if __name__ == "__main__":
    main()
