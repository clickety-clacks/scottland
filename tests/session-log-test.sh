#!/bin/bash
# Launcher-only fixture: no display, services or live files. Run on the test host.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d "$repo/build/session-log-test.XXXXXX")
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/data/scottland/dev/libexec" "$work/bin" "$work/runtime"
cat > "$work/data/scottland/dev/libexec/scottland-build-config" <<'SH'
#!/bin/sh
echo /dev/null
SH
cat > "$work/bin/wayfire" <<'SH'
#!/bin/sh
if [ "$TEST_SESSION" = first ]; then yes x | head -c 1048600; fi
echo "session=$TEST_SESSION core-limit=$(ulimit -c)"
exit 0
SH
cat > "$work/bin/systemctl" <<'SH'
#!/bin/sh
exit 1
SH
chmod +x "$work/bin/"* "$work/data/scottland/dev/libexec/"*
sessions=(first second third fourth fifth)
for ((index=0; index<${#sessions[@]}; index++)); do
  session=${sessions[index]}
  TEST_SESSION=$session SCOTTLAND_DEV_LAUNCHER=1 PATH="$work/bin:$PATH" \
    XDG_DATA_HOME="$work/data" XDG_STATE_HOME="$work/state" XDG_RUNTIME_DIR="$work/runtime" \
    "$repo/core/session/start-scottland"
  [[ $(tail -n 1 "$work/state/scottland/wayfire.log") == "session=$session core-limit=unlimited" ]]
  for ((age=1; age<=3; age++)); do
    suffix=$age
    (( age == 1 )) && suffix=previous || suffix=previous.$age
    log="$work/state/scottland/wayfire.log.$suffix"
    if (( age <= index )); then
      [[ -f $log && $(stat -c %s "$log") -le 1048576 ]]
      [[ $(tail -n 1 "$log") == "session=${sessions[index-age]} core-limit=unlimited" ]]
    else
      [[ ! -e $log ]]
    fi
  done
  [[ ! -e "$work/state/scottland/wayfire.log.previous.4" ]]
  echo "PASS launcher $session: core limit and three bounded previous logs"
done
