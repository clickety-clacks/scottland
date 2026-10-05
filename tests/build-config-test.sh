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
# GO23/GO28: Dye strength was renamed Dye density (2026-10-05). A value saved under the old key,
# by the settings app or by hand, reaches the session under the new one, and the last one wins.
cp "$repo/core/config.d/20-layout-settings" "$repo/core/config.d/90-user-overrides" "$work/hooks/config.d/"
printf '[scottland]\ngoo_dye_density = 1\n' >"$work/config/scottland/scottland.ini"
last_density() { sed -n 's/^goo_dye_density *= *//p' "$out" | tail -1; }
printf '[scottland]\ngoo_dye_strength = 1.5\n' >"$work/config/scottland/layout.ini"
SCOTTLAND_HOOKS="$work/hooks" XDG_CONFIG_HOME="$work/config" XDG_RUNTIME_DIR="$work/runtime" \
  "$repo/core/session/scottland-build-config" --output "$out" >/dev/null
if [ "$(last_density)" = 1.5 ] && ! grep -q '^goo_dye_strength' "$out"; then
  echo "PASS  a saved Dye strength carries over as Dye density"
else
  echo "FAIL  saved Dye strength: last goo_dye_density is '$(last_density)'"; fails=$((fails + 1))
fi
printf '[scottland]\n  goo_dye_strength=0.4\n' >"$work/config/scottland/overrides.ini"
SCOTTLAND_HOOKS="$work/hooks" XDG_CONFIG_HOME="$work/config" XDG_RUNTIME_DIR="$work/runtime" \
  "$repo/core/session/scottland-build-config" --output "$out" >/dev/null
if [ "$(last_density)" = 0.4 ]; then
  echo "PASS  a hand-written Dye strength override carries over and still wins"
else
  echo "FAIL  overridden Dye strength: last goo_dye_density is '$(last_density)'"; fails=$((fails + 1))
fi
[ "$fails" -eq 0 ] && echo "all build-config checks passed"
exit "$fails"
