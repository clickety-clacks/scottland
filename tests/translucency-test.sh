#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh isolated headless directory under build/}"
art="$PWD/build/translucency-evidence"
mkdir -p "$art"
cleanup(){ cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$art/wayfire.log" 2>/dev/null || true; tests/headless.sh stop >/dev/null 2>&1; }
trap cleanup EXIT
export SCOTTLAND_WIDGET_PATH=$PWD/tests/widgets
tests/headless.sh start --widgets
tests/headless.sh run python3 "$PWD/tests/translucency-test.py" "$art" | tee "$art/results.log"
