#!/bin/bash
# Isolated, repeatable 2560x1600 benchmark. No live session/config access.
set -euo pipefail
repo=$(realpath "${1:?repo}")
export SCOTTLAND_HEADLESS_DIR=${2:?fresh headless dir}
export SCOTTLAND_TEST_GOO=1 SCOTTLAND_TEST_OUTPUTS=1
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'runtime already exists' >&2; exit 1; }
runner=$(cd "$(dirname "$0")" && pwd)
cd "$repo"
tests/headless.sh start --widgets
trap 'tests/headless.sh stop >/dev/null 2>&1' EXIT
tests/headless.sh run python3 "$runner/goo-bench.py" "$SCOTTLAND_HEADLESS_DIR" "${3:-10}"
