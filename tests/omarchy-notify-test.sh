#!/bin/sh
# O35: Omarchy's notify hook answers scottland-notify's hook operations through the shell, and
# answers for glyphs and click commands only while the shell owns the notification bus name. Uses
# stand-in omarchy-shell, busctl and quickshell: never touches a real desktop or bus.
hook="$(cd "$(dirname "$0")/.." && pwd)/omarchy/notify.d/10-omarchy-notifications"
t=$(mktemp -d); trap 'rm -rf "$t"' EXIT
mkdir -p "$t/bin" "$t/nobin"
cat >"$t/bin/omarchy-shell" <<STUB
#!/bin/sh
printf '%s\n' "\$*" >>"$t/calls"
case "\$*" in
  "notifications ping") exit 0 ;;
  "-q omarchy.indicators refresh") exit 0 ;;
esac
if [ -e "$t/fail" ]; then cat "$t/fail" >&2; exit 1; fi
cat "$t/reply"
STUB
cat >"$t/bin/busctl" <<STUB
#!/bin/sh
echo "u \$(cat "$t/owner")"
STUB
cat >"$t/bin/quickshell" <<STUB
#!/bin/sh
printf '[{"config_path":"%s","id":"a1","launch_time":"t","pid":4242,"shell_id":"b2"}]\n' \
  "\$OMARCHY_PATH/shell/shell.qml"
STUB
chmod +x "$t/bin/omarchy-shell" "$t/bin/busctl" "$t/bin/quickshell"

# Prints the hook's stdout with each NUL shown as |, then its exit status.
run() {
  PATH="$t/bin:$PATH" OMARCHY_PATH=/opt/omarchy "$hook" "$@" >"$t/out" 2>/dev/null
  status=$?
  tr '\000\n' '| ' <"$t/out"
  printf 'exit %s' "$status"
}
reset() { rm -f "$t/calls" "$t/fail"; : >"$t/reply"; echo 4242 >"$t/owner"; }
called() { grep -qxF -e "$1" "$t/calls" && echo yes || echo no; }
fails=0
check() { if [ "$2" = "$3" ]; then echo "PASS  $1"; else echo "FAIL  $1 (got $2)"; fails=$((fails + 1)); fi; }

reset; echo ok >"$t/reply"
check "O35 dismiss answers 0 when the shell dismissed a card" "$(run dismiss Omarchy)" "exit 0"
check "O35 ...asking the shell without -q" "$(called "notifications dismiss Omarchy")" yes
reset; echo none >"$t/reply"
check "O35 dismiss answers 1 when nothing matched" "$(run dismiss Omarchy)" "exit 1"
reset; echo "omarchy-shell is not running" >"$t/fail"
check "O35 a shell that is not running is not this hook's receiver" "$(run dismiss Omarchy)" "exit 69"
reset; echo "omarchy-shell is not responding" >"$t/fail"
check "O35 a shell that stops responding is a failure" "$(run dismiss Omarchy)" "exit 70"

reset; echo off >"$t/reply"
check "O35 dnd-toggle prints the new state" "$(run dnd-toggle)" "off exit 0"
check "O35 ...and refreshes the indicator" "$(called "-q omarchy.indicators refresh")" yes
reset; echo maybe >"$t/reply"
check "O35 dnd-toggle fails on another answer" "$(run dnd-toggle)" "exit 70"

reset
check "O35 reminder-ui opens the shell's reminder panel" "$(run reminder-ui)" "exit 0"
check "O35 ...by summoning it" "$(called "shell summon omarchy.reminders {}")" yes
reset; echo "omarchy-shell is not running" >"$t/fail"
check "O35 reminder-ui with no shell is not this hook's" "$(run reminder-ui)" "exit 69"

reset
check "O35 glyph-hint names the shell's glyph hint" "$(run glyph-hint X)" "omarchy-glyph exit 0"
check "O35 a 17-code-unit glyph is one the shell drops" "$(run glyph-hint 12345678901234567)" "exit 65"
check "O35 a glyph with a control character is one the shell drops" "$(run glyph-hint "$(printf 'a\tb')")" "exit 65"
astral8=$(printf '\360\237\230\200\360\237\230\200\360\237\230\200\360\237\230\200\360\237\230\200\360\237\230\200\360\237\230\200\360\237\230\200')
check "O35 eight astral characters are 16 code units and draw" "$(run glyph-hint "$astral8")" "omarchy-glyph exit 0"
check "O35 nine are 18 and do not" "$(run glyph-hint "$astral8$(printf '\360\237\230\200')")" "exit 65"

check "O35 action-route carries the click argv" "$(run action-route click 1 true)" \
  'omarchy-exec-argv|["true"]|exit 0'
check "O35 action-route carries the buttons, Settings among them" \
  "$(run action-route click 1 true button Open 1 true button Settings 2 touch /tmp/s)" \
  'omarchy-exec-argv|["true"]|omarchy-actions|[{"label":"Open","argv":["true"]},{"label":"Settings","argv":["touch","/tmp/s"]}]|exit 0'
check "O35 argv words keep their spaces and quotes" \
  "$(run action-route click 2 xdg-open '/tmp/a "b".txt')" \
  'omarchy-exec-argv|["xdg-open","/tmp/a \"b\".txt"]|exit 0'
check "O35 a program starting with - is one the shell drops" "$(run action-route click 1 -x)" "exit 65"
check "O35 a blank label is one the shell drops" "$(run action-route click 1 true button " " 1 true)" "exit 65"
check "O35 33 words are more than the shell runs" \
  "$(run action-route click 33 true 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33)" \
  "exit 65"
long=$(printf '%300s' | tr ' ' x)
check "O35 a long label is shown cut, not dropped" "$(run action-route click 1 true button "$long" 1 true | sed 's/.*|exit/exit/')" "exit 0"

reset; echo 999 >"$t/owner"
check "O35 another receiver owning the bus name: glyph-hint is not this hook's" "$(run glyph-hint X)" "exit 69"
check "O35 ...nor action-route" "$(run action-route click 1 true)" "exit 69"

reset
check "O35 an operation the contract does not name is not this hook's" "$(run dnd-get)" "exit 69"
check "O35 ...and asks the shell nothing" "$([ -e "$t/calls" ] && echo asked || echo none)" none
check "O35 with no omarchy-shell every operation is not this hook's" \
  "$(PATH="$t/nobin:/usr/bin:/bin" "$hook" dismiss Omarchy >/dev/null 2>&1; echo $?)" 69

[ "$fails" -eq 0 ] && echo "all notify-hook checks passed"
exit "$fails"
