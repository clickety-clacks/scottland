#!/bin/bash
# Put this working tree on a test machine in dev mode, and optionally load it into the Scottland
# session running there.
#
#   tests/deploy.sh HOST [--reload | --tests-only]
#
# Syncs the repo (not ./build) to ~/Projects/scottland on HOST (SCOTTLAND_DEPLOY_DIR, relative to
# home, picks another folder), builds the plugin, and runs make dev-install. With --tests-only it
# builds the checkout's own test helpers (make test-hooks) instead, for headless tests there
# (tests/headless.sh with SCOTTLAND_HEADLESS_DIR), leaving the machine's sessions untouched. With --reload, reloads the running session in place via scottland-reload,
# which runs in that session's own recorded environment (no variables to set by hand).
# The machine may be shared (plumbus: other agents run tests there): this prints what's on its
# screen first, and never starts, stops or switches sessions.
set -euo pipefail
host=${1:?usage: tests/deploy.sh HOST [--reload]}
repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
dest=${SCOTTLAND_DEPLOY_DIR:-Projects/scottland}
revision=$(git -C "$repo" rev-parse --short=12 HEAD)
test_only=0
if [[ ${2:-} == --tests-only ]]; then
  test_only=1
  [[ -n ${SCOTTLAND_DEPLOY_DIR:-} ]] || {
    echo 'set SCOTTLAND_DEPLOY_DIR to a fresh per-run checkout path for --tests-only' >&2
    exit 2
  }
  rust_version=${MISE_RUST_VERSION:-}
  cargo_jobs=${CARGO_BUILD_JOBS:-2}
  [[ $rust_version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
    echo 'set MISE_RUST_VERSION to an exact stable version for --tests-only' >&2
    exit 2
  }
  [[ $cargo_jobs =~ ^[1-9][0-9]*$ ]] || {
    echo 'CARGO_BUILD_JOBS must be a positive integer' >&2
    exit 2
  }
  [[ $dest =~ ^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$ && "/$dest/" != *"/../"* && "/$dest/" != *"/./"* ]] || {
    echo 'SCOTTLAND_DEPLOY_DIR must be a safe relative path' >&2
    exit 2
  }
fi
if ((test_only)); then
  ssh "$host" "bash -s -- '$dest'" <<'REMOTE'
set -euo pipefail
target="$HOME/$1"
[[ ! -e $target && ! -L $target ]] || { echo 'test-only destination already exists; choose a fresh path' >&2; exit 2; }
mkdir -p -- "$(dirname -- "$target")"
mkdir -m 700 -- "$target"
REMOTE
  rsync -a --exclude build "$repo/" "$host:$dest/"
  ssh "$host" "MISE_RUST_VERSION=$rust_version CARGO_BUILD_JOBS=$cargo_jobs bash -s -- '$dest' '$revision'" <<'REMOTE'
set -euo pipefail
dest=$1
revision=$2
scratch_root="$HOME/.cache/scottland-build-tmp"
mkdir -p -- "$scratch_root"
TMPDIR=$(mktemp -d "$scratch_root/$revision.XXXXXXXX")
trap 'rm -rf -- "$TMPDIR"' EXIT
export TMPDIR MISE_RUST_VERSION CARGO_BUILD_JOBS
rustc_version=$(rustc --version)
cargo_version=$(cargo --version)
case $rustc_version in
  "rustc ${MISE_RUST_VERSION} ("*) ;;
  *) echo "expected Rust ${MISE_RUST_VERSION}; found: $rustc_version" >&2; exit 1 ;;
esac
case $cargo_version in
  "cargo ${MISE_RUST_VERSION} ("*) ;;
  *) echo "expected Cargo ${MISE_RUST_VERSION}; found: $cargo_version" >&2; exit 1 ;;
esac
cd -- "$HOME/$dest"
make test-hooks >/dev/null
echo "built with Rust ${MISE_RUST_VERSION}, CARGO_BUILD_JOBS=${CARGO_BUILD_JOBS}, and owned TMPDIR for $revision"
REMOTE
  exit 0
fi
rsync -a --delete --exclude build "$repo/" "$host:$dest/"
ssh "$host" 'set -e
  # Build scratch files under home: a shared machine's /tmp can be full of other users' files.
  export TMPDIR="$HOME/.cache/scottland-build-tmp"; mkdir -p "$TMPDIR"
  cd ~/Projects/scottland
  make plugin >/dev/null
  make dev-install >/dev/null
  echo "built and dev-installed $(git log --oneline -1)"
  echo "on screen: $(loginctl show-session "$(loginctl show-seat seat0 -p ActiveSession --value)" -p Desktop -p TTY --value 2>/dev/null | paste -sd" ")"
  echo "Scottland sessions:"; ~/.local/bin/scottland-exec --list || true'
if [[ ${2:-} == --reload ]]; then
  ssh "$host" '~/.local/bin/scottland-reload' 2>&1 | grep -v -i -e xkb -e "Using 0" -e "multiply defined"
fi
