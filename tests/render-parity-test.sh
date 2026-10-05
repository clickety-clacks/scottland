#!/bin/bash
# Window avoidance renders the same pixels as an older build, across output scales and transforms.
# A session starts on OLD_CHECKOUT's build (built with its own test helpers) and is reloaded in place
# between the two builds.
#   SCOTTLAND_HEADLESS_DIR=$OLD/build/hl-parity tests/render-parity-test.sh OLD_CHECKOUT [ARTIFACTS]
set -euo pipefail
cd "$(dirname "$0")/.."
new=$PWD
old=$(cd "${1:?usage: render-parity-test.sh OLD_CHECKOUT [ARTIFACTS]}" && pwd)
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh headless directory under the old checkout build/}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
[[ -f $old/build/libscottland.so && -f $new/build/libscottland.so ]] || { echo 'build both checkouts first' >&2; exit 1; }
artifacts=${2:-$new/build/render-parity}
mkdir -p "$artifacts"
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  (cd "$old" && tests/headless.sh stop) || true
  rm -f "$artifacts"/libscottland-*.so
}
trap cleanup EXIT
(cd "$old" && tests/headless.sh start)
(cd "$old" && tests/headless.sh run python3 -u "$new/tests/render-parity-test.py" "$artifacts" \
  "$old/build/libscottland.so" "$new/build/libscottland.so") | tee "$artifacts/render-parity.log"
exit "${PIPESTATUS[0]}"
