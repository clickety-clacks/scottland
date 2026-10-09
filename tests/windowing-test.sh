#!/bin/bash
# Run only on a test host. The caller supplies its own headless directory.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set an isolated headless test directory}"
artifacts=$SCOTTLAND_HEADLESS_DIR.windowing-artifacts
mkdir -p "$artifacts"
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop >/dev/null 2>&1
}
trap cleanup EXIT
tests/headless.sh stop >/dev/null 2>&1
tests/headless.sh start --widgets
tests/headless.sh run python3 tests/windowing-test.py "$artifacts"
