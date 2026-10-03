#!/bin/bash
# Run only on the test host. Evidence/logs stay on disk, never in the shared runtime.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a unique private test directory under build/}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
out=${1:?artifact directory}; shift
mkdir -p "$out"
sha256sum build/libscottland.so tests/goo-idle-bench.py > "$out/build.sha256"
stat -c '%y %n' build/libscottland.so > "$out/build-time.txt"
tests/headless.sh start --widgets
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$out/wayfire.log"
  tests/headless.sh stop
}
trap cleanup EXIT
tests/headless.sh run python3 tests/goo-idle-bench.py "$SCOTTLAND_HEADLESS_DIR" "$out" "$@"
