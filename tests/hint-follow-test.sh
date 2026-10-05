#!/bin/bash
# A cycled window's hint follows it while Alt is held (WK31/WK13), at output scales 1 and 2.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh headless directory under this checkout build/}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=${1:-$PWD/build/hint-follow-evidence}
mkdir -p "$artifacts"
trap 'tests/headless.sh stop' EXIT
status=0
for scale in 1 2; do
  tests/headless.sh start --widgets
  set +e
  tests/headless.sh run env FOLLOW_SCALE=$scale python3 -u tests/hint-follow-test.py "$artifacts/scale-$scale" |
    tee "$artifacts/scale-$scale.log"
  (( PIPESTATUS[0] == 0 )) || status=1
  set -e
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire-scale-$scale.log" 2>/dev/null || true
  tests/headless.sh stop
done
exit $status
