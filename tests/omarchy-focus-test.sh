#!/bin/sh
# O17: Omarchy's focus hook holds notifications (do-not-disturb) during full screen, and only turns
# off a do-not-disturb it turned on. Uses a stand-in omarchy-shell: never touches a real desktop.
hook="$(cd "$(dirname "$0")/.." && pwd)/omarchy/focus.d/10-omarchy-notifications"
t=$(mktemp -d); trap 'rm -rf "$t"' EXIT
mkdir -p "$t/bin" "$t/run"
cat >"$t/bin/omarchy-shell" <<STUB
#!/bin/sh
case "\$*" in
  "notifications dndState") cat "$t/state" ;;
  "notifications setDnd on") echo on >"$t/state" ;;
  "notifications setDnd off") echo off >"$t/state" ;;
esac
STUB
chmod +x "$t/bin/omarchy-shell"
run() { PATH="$t/bin:$PATH" XDG_RUNTIME_DIR="$t/run" "$hook" "$1"; cat "$t/state"; }
fails=0
check() { if [ "$2" = "$3" ]; then echo "PASS  $1"; else echo "FAIL  $1 (got $2)"; fails=$((fails + 1)); fi; }
echo off >"$t/state"
check "O17 full screen turns do-not-disturb on" "$(run on)" on
check "O17 ...and leaving it turns it back off" "$(run off)" off
echo on >"$t/state"
check "O17 a do-not-disturb the user had on stays on through full screen" "$(run on)/$(run off)" on/on
[ "$fails" -eq 0 ] && echo "all focus-hook checks passed"
exit "$fails"
