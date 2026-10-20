#!/usr/bin/env bash
set -euo pipefail

repo=$(cd -- "$(dirname -- "$0")/.." && pwd -P)
cd "$repo"
hdir=${SCOTTLAND_HEADLESS_DIR:?set a unique headless directory under this checkout build/}
[[ $(realpath -m -- "$hdir") == "$repo"/build/* ]] || {
  echo 'headless directory must be under this checkout build/' >&2
  exit 2
}
[[ ! -e $hdir && ! -L $hdir ]] || { echo 'headless directory already exists' >&2; exit 2; }
stamp=$(date +%s%N)
artifacts="$repo/build/window-notes-evidence-$stamp"
mkdir -m 700 -- "$artifacts"
cleanup() {
  tests/headless.sh stop >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

make test-hooks >"$artifacts/build.log" 2>&1 || {
  echo "make test-hooks failed; see $artifacts/build.log" >&2
  exit 1
}
tests/headless.sh start --widgets >"$artifacts/start.log" 2>&1 || {
  echo "headless start failed; see $artifacts/start.log" >&2
  exit 1
}
timeout --foreground 600s tests/headless.sh run python3 -u tests/window-notes-test.py "$artifacts" \
  2>&1 | tee "$artifacts/results.log"
