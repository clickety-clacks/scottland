#!/bin/bash
# GO21 pixel-exactness of the sleeping goo's cheap paths, in a private headless session.
#   tests/goo-exact-test.sh ARTIFACTS [--scale S] [--rotation normal|90|180|270] [--outputs 1|2]
# Needs a fresh SCOTTLAND_HEADLESS_DIR; SCOTTLAND_TEST_GOO_GLES=2 selects the packed path.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh isolated headless test directory}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=${1:?artifact directory}; shift
mkdir -p "$artifacts"
outputs=1
for ((i = 1; i <= $#; i++)); do [[ ${!i} == --outputs ]] && { j=$((i + 1)); outputs=${!j}; }; done
SCOTTLAND_TEST_GOO=1 SCOTTLAND_TEST_OUTPUTS=$outputs tests/headless.sh start --widgets
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop
}
trap cleanup EXIT
tests/headless.sh run python3 tests/goo-exact-test.py "$SCOTTLAND_HEADLESS_DIR" "$artifacts" "$@" | tee "$artifacts/results.log"
