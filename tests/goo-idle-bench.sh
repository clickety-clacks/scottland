#!/bin/bash
# Idle-desktop breathing cost (GO10/GO17), in a private headless session on the test machine.
#   tests/goo-idle-bench.sh ARTIFACT_DIR [SECONDS] [--visual]
# Needs SCOTTLAND_HEADLESS_DIR (a fresh path) in the environment. Never run on a daily machine.
set -euo pipefail
: "${SCOTTLAND_HEADLESS_DIR:?set a private SCOTTLAND_HEADLESS_DIR}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless dir already exists' >&2; exit 1; }
cd "$(dirname "$0")/.."
out=$(realpath -m "${1:?artifact dir}")
mkdir -p "$out"
export SCOTTLAND_TEST_GOO=1 SCOTTLAND_TEST_OUTPUTS=1
tests/headless.sh start --widgets
trap 'cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$out/wayfire.log" 2>/dev/null || true; tests/headless.sh stop >/dev/null 2>&1' EXIT
tests/headless.sh run python3 tests/goo-idle-bench.py "$SCOTTLAND_HEADLESS_DIR" "$out" "${@:2}"
