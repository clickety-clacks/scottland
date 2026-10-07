#!/bin/bash
# Run the pointer-slice middle-press acceptance checks in an isolated Wayfire session.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh, unique headless directory for this checkout}"
headless_dir=$(realpath -m "$SCOTTLAND_HEADLESS_DIR")
case "$headless_dir/" in
  "$PWD"/build/*/) ;;
  *) echo 'SCOTTLAND_HEADLESS_DIR must be under this checkout build/' >&2; exit 1 ;;
esac
[[ ! -e $headless_dir ]] || {
  echo "headless directory occupied: $headless_dir" >&2
  exit 1
}
tmp_dir="${headless_dir}.tmp"
[[ ! -e $tmp_dir ]] || { echo "TMPDIR occupied: $tmp_dir" >&2; exit 1; }
mkdir -p "$tmp_dir"
export SCOTTLAND_HEADLESS_DIR=$headless_dir TMPDIR=$tmp_dir
artifacts=${1:-$PWD/build/middle-press-evidence}
mkdir -p "$artifacts"
started=0
cleanup() {
  if ((started)); then
    if [[ -f $headless_dir/display ]]; then
      cp "$headless_dir/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
      tests/headless.sh stop
    elif [[ -e $headless_dir ]]; then
      rm -rf "$headless_dir"
    fi
  fi
  rm -rf "$tmp_dir"
}
trap cleanup EXIT
started=1
SCOTTLAND_TEST_OUTPUTS=1 tests/headless.sh start
tests/headless.sh run python3 -u tests/middle-press-test.py "$artifacts" | tee "$artifacts/test.log"
