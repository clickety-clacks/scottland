#!/bin/bash
# Reload rehearsal (AGENTS.md testing step 4): start a headless session on an older checkout's build
# (built with its own test helpers), then reload it in place into this checkout's plugin.
#   SCOTTLAND_HEADLESS_DIR=$OLD/build/hl-rehearsal tests/reload-rehearsal-test.sh OLD_CHECKOUT [ARTIFACTS]
set -euo pipefail
cd "$(dirname "$0")/.."
new=$PWD
old=$(cd "${1:?usage: reload-rehearsal-test.sh OLD_CHECKOUT [ARTIFACTS]}" && pwd)
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh headless directory under the old checkout build/}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
[[ -f $old/build/libscottland.so && -f $new/build/libscottland.so ]] || { echo 'build both checkouts first' >&2; exit 1; }
artifacts=${2:-$new/build/reload-rehearsal}
mkdir -p "$artifacts"
# The session reads its settings metadata from the old checkout; the reload registers the new one
# there, as scottland-reload does through the installed metadata. Put the old file back afterwards.
session_xml=$old/core/plugin/metadata/scottland.xml
cp "$session_xml" "$artifacts/scottland.xml.old"
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  (cd "$old" && tests/headless.sh stop) || true
  cp "$artifacts/scottland.xml.old" "$session_xml"
  rm -rf "$SCOTTLAND_HEADLESS_DIR" "$artifacts"/plugins
}
trap cleanup EXIT
(cd "$old" && SCOTTLAND_TEST_OUTPUTS=1 tests/headless.sh start --widgets)
(cd "$old" && tests/headless.sh run python3 -u "$new/tests/reload-rehearsal-test.py" "$artifacts" "$new/build/libscottland.so" \
  "$session_xml" "$new/core/plugin/metadata/scottland.xml") |
  tee "$artifacts/reload-rehearsal.log"
exit "${PIPESTATUS[0]}"
