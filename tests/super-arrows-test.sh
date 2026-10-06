#!/bin/bash
# WK40 Super+arrows window navigation, in a private headless session with real input.
#   tests/super-arrows-test.sh ARTIFACTS [SCENARIO ...]
#   tests/super-arrows-test.sh ARTIFACTS --rehearse OLD_CHECKOUT [SCENARIO ...]
# Scenarios: cross zones hidden concentric peek (default), screens (starts the session with two screens).
# --rehearse starts the session on OLD_CHECKOUT's build (built with make test-hooks) and reloads it
# in place into this checkout's plugin first (AGENTS.md testing step 4), then runs the scenarios.
# Needs a fresh (not yet existing) SCOTTLAND_HEADLESS_DIR under the session checkout's build/.
set -euo pipefail
cd "$(dirname "$0")/.."
new=$PWD
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh isolated headless test directory}"
# A fresh path this run creates, so cleanup removes only what it made.
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory exists: give a fresh one' >&2; exit 1; }
artifacts=$(realpath -m "${1:?artifact directory}"); shift
session=$new; reload=()
if [[ ${1:-} == --rehearse ]]; then
  session=$(cd "${2:?old checkout}" && pwd); shift 2
  [[ -f $session/build/libscottland.so && -f $new/build/libscottland.so ]] || { echo 'build both checkouts first' >&2; exit 1; }
  # The session reads its settings metadata from the old checkout; put that file back afterwards.
  session_xml=$session/core/plugin/metadata/scottland.xml
  mkdir -p "$artifacts"; cp "$session_xml" "$artifacts/scottland.xml.old"
  reload=(--reload "$new/build/libscottland.so" "$session_xml" "$new/core/plugin/metadata/scottland.xml")
fi
[[ $(realpath -m "$SCOTTLAND_HEADLESS_DIR") == "$session"/build/* ]] || { echo "use a directory under $session/build/" >&2; exit 1; }
mkdir -p "$artifacts"
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  (cd "$session" && tests/headless.sh stop) || true
  [[ -z ${session_xml:-} ]] || cp "$artifacts/scottland.xml.old" "$session_xml"
  rm -rf "$SCOTTLAND_HEADLESS_DIR" "$artifacts"/libscottland-rehearsal-*.so
}
trap cleanup EXIT
trap 'exit 143' INT TERM
outputs=1; [[ " $* " == *' screens '* ]] && outputs=2
(cd "$session" && SCOTTLAND_TEST_OUTPUTS=$outputs tests/headless.sh start --widgets)
(cd "$session" && timeout --foreground 600s tests/headless.sh run python3 -u "$new/tests/super-arrows-test.py" \
  "$artifacts" "$@" "${reload[@]}") | tee "$artifacts/results.log"
exit "${PIPESTATUS[0]}"
