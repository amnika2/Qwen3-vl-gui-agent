#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
task_root="$PWD"
task_node="$task_root/vendor/runtime/node-v22.16.0-linux-x64"
if [ ! -x "$task_node/bin/node" ]; then
  curl -fL https://nodejs.org/dist/v22.16.0/node-v22.16.0-linux-x64.tar.xz -o vendor/runtime/node.tar.xz
  tar -xJf vendor/runtime/node.tar.xz -C vendor/runtime
fi
export PATH="$task_node/bin:$PATH"
if [ ! -f vendor/runtime/composer.phar ]; then
  curl -fL https://getcomposer.org/download/2.9.2/composer.phar -o vendor/runtime/composer.phar
fi
for task_app in akaunting easyappointments ampache; do
  vendor/runtime/php vendor/runtime/composer.phar install --working-dir="vendor/apps/$task_app/base" --no-dev --no-interaction --no-scripts --prefer-dist
  npm ci --prefix "vendor/apps/$task_app/base" --no-audit --no-fund
done
npm run production --prefix vendor/apps/akaunting/base
npm run build --prefix vendor/apps/ampache/base
(cd vendor/apps/easyappointments/base && npx --no-install gulp compile)
python scripts/prepare_anchor_deps.py
vendor/runtime/php vendor/runtime/composer.phar install --working-dir=vendor/anchor-deps --no-dev --no-interaction --no-scripts --prefer-dist
