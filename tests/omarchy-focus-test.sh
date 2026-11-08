#!/bin/sh
# O17: Omarchy's focus hook holds notifications (do-not-disturb) during full screen, and only turns
# off a do-not-disturb it turned on. Uses a stand-in omarchy-shell: never touches a real desktop.
hook="$(cd "$(dirname "$0")/.." && pwd)/omarchy/focus.d/10-omarchy-notifications"
t=$(mktemp -d); trap 'rm -rf "$t"' EXIT
mkdir -p "$t/bin" "$t/run"
cat >"$t/bin/omarchy-shell" <<STUB
#!/bin/sh
case "\$*" in
  "notifications isDnd")
    [ ! -e "\$TEST_STATE_DIR/fail-isDnd" ] || exit 23
    cat "\$TEST_STATE_DIR/state"
    ;;
  "notifications setDnd on")
    echo set-on >>"\$TEST_STATE_DIR/calls"
    echo on >"\$TEST_STATE_DIR/state"
    ;;
  "notifications setDnd off")
    echo set-off >>"\$TEST_STATE_DIR/calls"
    [ ! -e "\$TEST_STATE_DIR/fail-set-off" ] || exit 25
    echo off >"\$TEST_STATE_DIR/state"
    ;;
esac
STUB
chmod +x "$t/bin/omarchy-shell"
mark="$t/run/scottland/focus-dnd"
run() { PATH="$t/bin:$PATH" TEST_STATE_DIR="$t" XDG_RUNTIME_DIR="$t/run" "$hook" "$1"; }
fails=0
check() { if [ "$2" = "$3" ]; then echo "PASS  $1"; else echo "FAIL  $1 (got $2)"; fails=$((fails + 1)); fi; }
echo off >"$t/state"
: >"$t/calls"
run on
check "O17 full screen turns do-not-disturb on" "$(cat "$t/state")" on
check "O17 records ownership of the DND change" "$(test -e "$mark" && echo yes || echo no)" yes
run off
check "O17 leaving full screen turns its DND back off" "$(cat "$t/state")" off
check "O17 clears ownership after turning DND off" "$(test -e "$mark" && echo yes || echo no)" no
echo on >"$t/state"
: >"$t/calls"
run on
run off
check "O17 a do-not-disturb the user had on stays on" "$(cat "$t/state")" on
check "O17 leaves pre-existing DND unclaimed" "$(test -e "$mark" && echo yes || echo no)" no
check "O17 does not call the setter for pre-existing DND" "$(cat "$t/calls")" ""
echo off >"$t/state"
rm -f "$t/fail-set-off"
touch "$t/fail-isDnd"
: >"$t/calls"
run on
check "O17 receiver query failure leaves DND unchanged" "$(cat "$t/state")" off
check "O17 receiver query failure does not claim DND" "$(test -e "$mark" && echo yes || echo no)" no
check "O17 receiver query failure does not call the setter" "$(cat "$t/calls")" ""
rm -f "$t/fail-isDnd"
echo on >"$t/state"
mkdir -p "$(dirname "$mark")"
touch "$mark" "$t/fail-set-off"
run off
check "O17 failed DND-off receiver call leaves state on" "$(cat "$t/state")" on
check "O17 failed DND-off keeps ownership for a later retry" "$(test -e "$mark" && echo yes || echo no)" yes
[ "$fails" -eq 0 ] && echo "all focus-hook checks passed"
exit "$fails"
