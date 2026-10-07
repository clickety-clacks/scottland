#!/bin/bash
# Owner entrypoint for the WK37/WK38 item2 checks plus the tint pixel check.
set -euo pipefail
umask 077

case ${I:-} in
  tint) ;;
  *) echo 'set I=tint to run the item2 and tint pixel checks' >&2; exit 2 ;;
esac

repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo"
[[ -x tests/hint-outline-test.sh && -f tests/hint-outline-test.py && \
   -f tests/window-mode-tint-pixel-test.py ]] || {
  echo 'item2 WK37/WK38 or composed tint pixel source is missing' >&2; exit 2;
}

mkdir -p "$repo/build"
build=$(realpath -m -- "$repo/build")
scratch=$(mktemp -d -- "$build/item2-tint.XXXXXXXX")
chmod 700 -- "$scratch"
tmp="$scratch/tmp"
mkdir -m 700 -- "$tmp"
export TMPDIR=$tmp
export SCOTTLAND_HEADLESS_ISOLATION=1 SCOTTLAND_TEST_SCRATCH=$scratch
unset SCOTTLAND_SESSION_DIR

finish() {
  result=$?
  trap - EXIT
  if [[ -n ${SCOTTLAND_HEADLESS_DIR:-} && ( -e $SCOTTLAND_HEADLESS_DIR || -L $SCOTTLAND_HEADLESS_DIR ) ]]; then
    tests/headless.sh stop || result=1
  fi
  if [[ -n ${SCOTTLAND_HEADLESS_DIR:-} && ( -e $SCOTTLAND_HEADLESS_DIR || -L $SCOTTLAND_HEADLESS_DIR ) ]]; then
    echo "headless cleanup did not release its private directory: $SCOTTLAND_HEADLESS_DIR" >&2
    result=1
  fi
  if [[ -d $tmp && ! -L $tmp && $(realpath -m -- "$tmp") == "$scratch/tmp" && \
        $(stat -c '%u:%a' -- "$tmp") == "$(id -u):700" ]]; then
    rm -rf -- "$tmp"
  else
    echo 'temporary directory ownership changed; preserving runner scratch' >&2
    result=1
  fi
  exit "$result"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

export SCOTTLAND_HEADLESS_DIR="$scratch/headless-wk37-wk38"
export SCOTTLAND_TEST_EVIDENCE_DIR="$scratch/evidence-wk37-wk38"
mkdir -m 700 -- "$SCOTTLAND_TEST_EVIDENCE_DIR"
tests/hint-outline-test.sh
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || {
  echo 'WK37/WK38 headless session was not cleaned from its private scratch' >&2; exit 1;
}

export SCOTTLAND_HEADLESS_DIR="$scratch/headless-tint"
export SCOTTLAND_TEST_GOO=1
export SCOTTLAND_TEST_EVIDENCE_DIR="$scratch/evidence-tint"
mkdir -m 700 -- "$SCOTTLAND_TEST_EVIDENCE_DIR"
tests/headless.sh start --widgets
tests/headless.sh run python3 tests/window-mode-tint-pixel-test.py "$SCOTTLAND_TEST_EVIDENCE_DIR" \
  | tee "$SCOTTLAND_TEST_EVIDENCE_DIR/results.log"
tests/headless.sh stop
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || {
  echo 'tint headless session was not cleaned from its private scratch' >&2; exit 1;
}
echo "evidence retained under $scratch"
