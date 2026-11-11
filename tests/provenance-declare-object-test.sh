#!/usr/bin/env bash
set -euo pipefail

repo=$(cd -- "$(dirname -- "$0")/.." && pwd -P)
cd "$repo"
build=$(realpath -e -- "$repo/build")
[[ -x build/hooks/libexec/scottland-exec && -f build/libscottland.so ]] || {
  echo 'build this exact checkout with make test-hooks before running this focused regression' >&2
  exit 1
}

run_root=$(mktemp -d "$build/provenance-declare-object.XXXXXXXX")
headless="$run_root/headless"
tmp="$run_root/tmp"
mkdir -m 700 -- "$tmp"
export SCOTTLAND_HEADLESS_DIR="$headless"
export TMPDIR="$tmp"

cleanup() {
  status=$?
  trap - EXIT
  if [[ -f $headless/wayfire.log ]]; then
    cp -- "$headless/wayfire.log" "$run_root/wayfire.log" || true
  fi
  tests/headless.sh stop --preserve-scratch >"$run_root/headless-stop.log" 2>&1 || true
  rm -rf -- "$headless" "$tmp"
  exit "$status"
}
trap cleanup EXIT

tests/headless.sh start >"$run_root/headless-start.log" 2>&1

pid=$BASHPID
valid_request=$(printf '{"pid":%s,"name":"pv4-object-size-regression","launcher":"focused-regression"}' "$pid")
tests/headless.sh ipc scottland/provenance-declare "$valid_request" >"$run_root/valid-response.json"
python3 - "$run_root/valid-response.json" <<'PY'
import json
import pathlib
import sys

reply = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert isinstance(reply.get("token"), str) and reply["token"], reply
assert isinstance(reply.get("launched_at"), int) and reply["launched_at"] > 0, reply
PY

unknown_request=$(printf '{"pid":%s,"name":"pv4-object-size-regression","unknown":true}' "$pid")
tests/headless.sh ipc scottland/provenance-declare "$unknown_request" >"$run_root/unknown-field-response.json"
python3 - "$run_root/unknown-field-response.json" <<'PY'
import json
import pathlib
import sys

reply = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert "unknown field" in reply.get("error", ""), reply
assert "token" not in reply, reply
PY

printf 'PASS: valid object declaration returned a token and launch time; unknown object field was rejected without a compositor crash.\n'
printf 'Evidence: %s\n' "$run_root"
