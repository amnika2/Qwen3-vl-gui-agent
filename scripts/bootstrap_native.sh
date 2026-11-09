#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p vendor/runtime/debs
cd vendor/runtime/debs
apt-get download php8.3-cli php8.3-common php8.3-mysql php8.3-mbstring php8.3-xml \
  php8.3-curl php8.3-zip php8.3-bcmath php8.3-gd php8.3-intl mariadb-server \
  mariadb-server-core mariadb-client-core mariadb-client mariadb-common \
  liburing2 libaio1t64 libonig5 libzip4t64 libmariadb3 \
  libatk1.0-0t64 libatk-bridge2.0-0t64 libxcomposite1 libxdamage1 libxfixes3 libasound2t64 libatspi2.0-0t64 libxi6 libxtst6
for package in *.deb; do dpkg-deb -x "$package" ../root; done
sha256sum ./*.deb > ../debs.sha256
cd ../../..
python scripts/configure_php.py
