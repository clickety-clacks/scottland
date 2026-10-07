#!/bin/bash
# Owner entrypoint for item2's isolated visual checks.
set -euo pipefail
umask 077

case ${I:-} in
  tint|acceptance) ;;
  *) echo 'set I=tint for WK37/WK38 and WK36, or I=acceptance to include WK41/WK42' >&2; exit 2 ;;
esac

repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo"
[[ -x tests/hint-outline-test.sh && -f tests/hint-outline-test.py && \
   -f tests/pairing-opaque-test.py ]] || {
  echo 'item2 WK37/WK38 or pairing opaque-once source is missing' >&2; exit 2;
}
if [[ $I == acceptance ]]; then
  [[ -f tests/hint-stuck-offset-test.py && -f tests/hint-background-opacity-test.py ]] || {
    echo 'item2 WK41 or WK42 test source is missing' >&2; exit 2;
  }
fi

mkdir -p "$repo/build"
build=$(realpath -m -- "$repo/build")
scratch=$(mktemp -d -- "$build/item2-$I.XXXXXXXX")
chmod 700 -- "$scratch"
tmp="$scratch/tmp"
mkdir -m 700 -- "$tmp"
export TMPDIR=$tmp
export SCOTTLAND_HEADLESS_ISOLATION=1 SCOTTLAND_TEST_SCRATCH=$scratch
unset SCOTTLAND_SESSION_DIR

finish() {
  result=$?
  trap - EXIT
  cleanup_ok=1
  if [[ -n ${SCOTTLAND_HEADLESS_DIR:-} && ( -e $SCOTTLAND_HEADLESS_DIR || -L $SCOTTLAND_HEADLESS_DIR ) ]]; then
    tests/headless.sh stop || { result=1; cleanup_ok=0; }
  fi
  if [[ -n ${SCOTTLAND_HEADLESS_DIR:-} && ( -e $SCOTTLAND_HEADLESS_DIR || -L $SCOTTLAND_HEADLESS_DIR ) ]]; then
    echo "headless cleanup did not release its private directory: $SCOTTLAND_HEADLESS_DIR" >&2
    result=1; cleanup_ok=0
  fi
  if ((cleanup_ok)) && \
     [[ -d $scratch && ! -L $scratch && $(realpath -m -- "$scratch") == "$scratch" && \
        $(stat -c '%u:%a' -- "$scratch") == "$(id -u):700" && \
        -d $tmp && ! -L $tmp && $(realpath -m -- "$tmp") == "$scratch/tmp" && \
        $(stat -c '%u:%a' -- "$tmp") == "$(id -u):700" ]]; then
    rm -rf -- "$tmp" || { echo 'temporary scratch cleanup failed; preserving runner scratch' >&2; result=1; }
  else
    echo 'headless cleanup or scratch ownership check failed; preserving runner scratch' >&2
    result=1
  fi
  exit "$result"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

export SCOTTLAND_HEADLESS_DIR="$scratch/headless-wk37-wk38"
export SCOTTLAND_TEST_EVIDENCE_DIR="$scratch/evidence-wk37-wk38"
unset SCOTTLAND_TEST_GOO
mkdir -m 700 -- "$SCOTTLAND_TEST_EVIDENCE_DIR"
tests/hint-outline-test.sh
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || {
  echo 'WK37/WK38 headless session was not cleaned from its private scratch' >&2; exit 1;
}

export SCOTTLAND_HEADLESS_DIR="$scratch/headless-pairing-opaque"
export SCOTTLAND_TEST_EVIDENCE_DIR="$scratch/evidence-pairing-opaque"
mkdir -m 700 -- "$SCOTTLAND_TEST_EVIDENCE_DIR"
tests/headless.sh start
tests/headless.sh run timeout --signal=TERM --kill-after=30s 4m python3 -u \
  tests/pairing-opaque-test.py "$SCOTTLAND_TEST_EVIDENCE_DIR" \
  | tee "$SCOTTLAND_TEST_EVIDENCE_DIR/results.log"
cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$SCOTTLAND_TEST_EVIDENCE_DIR/wayfire.log" 2>/dev/null || true
tests/headless.sh stop
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || {
  echo 'pairing opaque-once headless session was not cleaned from its private scratch' >&2; exit 1;
}
echo "evidence retained under $scratch"

if [[ $I == acceptance ]]; then
  run_owned_suite() {
    local name=$1 widgets=$2 test_script=$3 goo=$4
    local status=0
    export SCOTTLAND_HEADLESS_DIR="$scratch/headless-$name"
    export SCOTTLAND_TEST_EVIDENCE_DIR="$scratch/evidence-$name"
    mkdir -m 700 -- "$SCOTTLAND_TEST_EVIDENCE_DIR"
    if [[ $goo == on ]]; then
      export SCOTTLAND_TEST_GOO=1
    elif [[ $goo == off ]]; then
      export SCOTTLAND_TEST_GOO=0
    else
      unset SCOTTLAND_TEST_GOO
    fi

    if [[ $widgets == yes ]]; then
      tests/headless.sh start --widgets || return 2
    else
      tests/headless.sh start || return 2
    fi
    tests/headless.sh run timeout --signal=TERM --kill-after=30s 4m \
      python3 -u "$test_script" "$SCOTTLAND_TEST_EVIDENCE_DIR" \
      | tee "$SCOTTLAND_TEST_EVIDENCE_DIR/results.log" || status=1
    cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$SCOTTLAND_TEST_EVIDENCE_DIR/wayfire.log" || status=1
    tests/headless.sh stop || {
      echo "owned headless stop failed for $name; preserving runner scratch" >&2
      return 2
    }
    [[ ! -e $SCOTTLAND_HEADLESS_DIR && ! -L $SCOTTLAND_HEADLESS_DIR ]] || {
      echo "owned headless path remains for $name; preserving runner scratch" >&2
      return 2
    }
    return "$status"
  }

  acceptance_status=0
  if run_owned_suite wk41-stuck-offset no tests/hint-stuck-offset-test.py default; then
    :
  else
    suite_status=$?
    ((suite_status == 2)) && exit 1
    acceptance_status=1
  fi
  if run_owned_suite wk42-opacity-goo-on yes tests/hint-background-opacity-test.py on; then
    :
  else
    suite_status=$?
    ((suite_status == 2)) && exit 1
    acceptance_status=1
  fi
  if run_owned_suite wk42-opacity-goo-off yes tests/hint-background-opacity-test.py off; then
    :
  else
    suite_status=$?
    ((suite_status == 2)) && exit 1
    acceptance_status=1
  fi
  ((acceptance_status == 0)) || exit "$acceptance_status"
  echo "WK41/WK42 evidence retained under $scratch"
fi
