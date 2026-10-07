#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh headless directory under this checkout build/}"
source tests/headless-runner-isolation.sh
artifacts=${1:-$SCOTTLAND_HEADLESS_DIR.window-double-tap-artifacts}
if (($#)); then shift; fi
trap scottland_runner_cleanup EXIT
scottland_runner_prepare "$artifacts"
scottland_runner_runtime_unchanged
tests/headless.sh start --widgets
scottland_runner_runtime_unchanged
tests/headless.sh run python3 -u tests/window-double-tap-test.py "$SCOTTLAND_RUNNER_ARTIFACTS" "$@" | tee "$SCOTTLAND_RUNNER_ARTIFACTS/results.log"
scottland_runner_runtime_unchanged
