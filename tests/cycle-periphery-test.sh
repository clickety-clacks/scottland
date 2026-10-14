#!/bin/bash
# WP4/WP8 Window-mode cycles reach the periphery, with real input and Mike's zone settings
# (tests/cycle-periphery-test.py). Run only on an isolated shared test runner, never on a daily
# desktop. The caller supplies a fresh headless directory under this checkout's build/.
# Builds this checkout's plugin and test helpers first (make test-hooks): without them
# tests/headless.sh would fall back to whatever helpers the machine has dev-installed.
set -euo pipefail
cd "$(dirname "$0")/.."
source tests/headless-runner-isolation.sh
: "${SCOTTLAND_HEADLESS_DIR:?set an isolated headless test directory}"
artifacts=${1:-$SCOTTLAND_HEADLESS_DIR.cycle-periphery-artifacts}
trap scottland_runner_cleanup EXIT
scottland_runner_prepare "$artifacts"
scottland_runner_runtime_unchanged
make test-hooks >"$SCOTTLAND_RUNNER_ARTIFACTS/build.log" 2>&1 || {
  echo "make test-hooks failed; see $SCOTTLAND_RUNNER_ARTIFACTS/build.log" >&2
  exit 1
}
[[ -x build/hooks/libexec/scottland-exec && -f build/libscottland.so ]] ||
  { echo "this checkout's test helpers are missing after make test-hooks" >&2; exit 1; }
tests/headless.sh start --widgets
scottland_runner_runtime_unchanged
python3 tests/cycle-periphery-test.py "$SCOTTLAND_RUNNER_ARTIFACTS"
scottland_runner_runtime_unchanged
