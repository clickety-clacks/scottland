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
rsync -a --delete --exclude build "$repo/" "$host:$dest/"
if [[ ${2:-} == --tests-only ]]; then
  ssh "$host" "set -e; export TMPDIR=\"\$HOME/.cache/scottland-build-tmp\"; mkdir -p \"\$TMPDIR\"
    cd ~/$dest && make test-hooks >/dev/null && echo \"built with its own test helpers: \$(git log --oneline -1)\""
  exit 0
fi
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
