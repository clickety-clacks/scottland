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
echo "session=$TEST_SESSION core-limit=$(ulimit -c)"
exit 0
SH
cat > "$work/bin/systemctl" <<'SH'
#!/bin/sh
exit 1
SH
chmod +x "$work/bin/"* "$work/data/scottland/dev/libexec/"*
for session in first second third; do
  TEST_SESSION=$session SCOTTLAND_DEV_LAUNCHER=1 PATH="$work/bin:$PATH" \
    XDG_DATA_HOME="$work/data" XDG_STATE_HOME="$work/state" XDG_RUNTIME_DIR="$work/runtime" \
    "$repo/core/session/start-scottland"
  [[ $(cat "$work/state/scottland/wayfire.log") == "session=$session core-limit=unlimited" ]]
  case $session in
    first) [[ ! -e "$work/state/scottland/wayfire.log.previous" ]] ;;
    second) [[ $(cat "$work/state/scottland/wayfire.log.previous") == 'session=first core-limit=unlimited' ]] ;;
    third) [[ $(cat "$work/state/scottland/wayfire.log.previous") == 'session=second core-limit=unlimited' ]] ;;
  esac
  echo "PASS launcher $session: core limit and previous log"
done
