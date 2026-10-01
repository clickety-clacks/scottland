#!/bin/sh
# E7: concurrent config builds never leave the config empty (the config watcher and
# scottland-reload rebuilding at once used to truncate it). Runs without a session.
set -u
repo=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/hooks/config.d" "$work/config/scottland" "$work/runtime"
printf '[core]\nplugins = move\n[move]\nenable_snap = false\n' >"$work/config/scottland/scottland.ini"
out="$work/out.ini"
fails=0
for round in 1 2 3 4 5; do
  for i in $(seq 1 20); do
    SCOTTLAND_HOOKS="$work/hooks" XDG_CONFIG_HOME="$work/config" XDG_RUNTIME_DIR="$work/runtime" \
      "$repo/core/session/scottland-build-config" --output "$out" >/dev/null 2>&1 &
  done
  wait
  if grep -q '^enable_snap = false$' "$out" && [ -z "$(ls "$out".* 2>/dev/null)" ]; then
    echo "PASS  E7 round $round: 20 concurrent builds leave the whole config, no temporary files"
  else
    echo "FAIL  E7 round $round: config is $(wc -c <"$out") bytes"; fails=$((fails + 1))
  fi
done
[ "$fails" -eq 0 ] && echo "all build-config checks passed"
exit "$fails"
