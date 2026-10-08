#!/bin/bash
set -euo pipefail

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
scratch=$(mktemp -d /tmp/scottland-headless-path-guard.XXXXXXXX)
link="$repo/.headless-path-guard-link-${BASHPID}-${RANDOM}"
link_created=0
expected='headless checkout resolves under /tmp, which this harness masks with its private TMPDIR; stage the checkout outside /tmp'

cleanup() {
  status=$?
  trap - EXIT
  if ((link_created)); then rm -f -- "$link"; fi
  rm -rf -- "$scratch"
  exit "$status"
}
trap cleanup EXIT

mkdir -p -- "$scratch/checkout/tests"
cp -- "$repo/tests/headless.sh" "$scratch/checkout/tests/headless.sh"
chmod +x -- "$scratch/checkout/tests/headless.sh"

assert_rejected_before_scratch() {
  local script_path=$1 error_path=$2 status=0
  "$script_path" start >/dev/null 2>"$error_path" || status=$?
  [[ $status == 2 ]] || {
    cat -- "$error_path" >&2
    echo "expected /tmp checkout guard exit 2, got $status" >&2
    return 1
  }
  grep -Fqx -- "$expected" "$error_path" || {
    cat -- "$error_path" >&2
    echo 'headless checkout guard diagnostic did not match' >&2
    return 1
  }
}

assert_rejected_before_scratch "$scratch/checkout/tests/headless.sh" "$scratch/direct.err"
[[ ! -e $scratch/checkout/build ]] || {
  echo 'headless checkout guard created build scratch before rejecting the path' >&2
  exit 1
}
mkdir -p -- "$scratch/checkout/build/headless"
printf '%s\n' 'preserve this evidence' >"$scratch/checkout/build/headless/wayfire.log"
ln -s -- "$scratch/checkout" "$link"
link_created=1
assert_rejected_before_scratch "$link/tests/headless.sh" "$scratch/symlink.err"
[[ $(cat -- "$scratch/checkout/build/headless/wayfire.log") == 'preserve this evidence' ]] || {
  echo 'headless checkout guard altered existing scratch evidence' >&2
  exit 1
}

echo 'headless path guard rejects direct and symlink-resolved /tmp checkouts before scratch creation'
