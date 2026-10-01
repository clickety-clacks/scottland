#!/bin/bash
# Presenting a window (L30) in a headless Scottland with the widget service: a side window flies to
# the middle at 100%, a widget opens back into its window, a center window stays put. Windows are
# placed with real input (stipc Super+drag); `scottland/present` is the request under test.
#
#   tests/headless.sh start --widgets && tests/present-test.sh
set -uo pipefail
cd "$(dirname -- "$0")/.."
fails=0
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; fails=$((fails + 1)); }

h()   { tests/headless.sh ipc "$@" >/dev/null; }
ipc() { tests/headless.sh ipc "$@"; }
view() {  # view <title> <python expr over v>
  ipc scottland/layout-state | python3 -c "
import json,sys
for v in json.load(sys.stdin)['views']:
    if v['title'] == sys.argv[1] and not v['widget']:
        print($2); break" "$1"
}
active() { ipc window-rules/get-focused-view | python3 -c "import json,sys; print(json.load(sys.stdin)['info']['title'])"; }
super_drag() {  # super_drag x1 y1 x2 y2
  h stipc/move_cursor "{\"x\":$1,\"y\":$2}"; sleep 0.2
  h stipc/feed_key '{"key":"KEY_LEFTMETA","state":true}'
  h stipc/feed_button '{"combo":"BTN_LEFT","mode":"press"}'
  for i in 1 2 3 4 5 6 7 8 9 10; do
    h stipc/move_cursor "{\"x\":$(( $1 + ($3 - $1) * i / 10 )),\"y\":$(( $2 + ($4 - $2) * i / 10 ))}"; sleep 0.03
  done
  sleep 0.3
  h stipc/feed_button '{"combo":"BTN_LEFT","mode":"release"}'
  h stipc/feed_key '{"key":"KEY_LEFTMETA","state":false}'
  sleep 0.6
}
open_window() {  # open_window <title>: a terminal whose title stays put
  (tests/headless.sh run foot -T "$1" sleep 600 >/dev/null 2>&1 &)
  for _ in $(seq 40); do [[ -n $(view "$1" "v['id']") ]] && return; sleep 0.1; done
}
center() { view "$1" "f\"{v['frame']['x'] + v['frame']['width'] / 2:.0f} {v['frame']['y'] + v['frame']['height'] / 2:.0f}\""; }

width=$(ipc window-rules/list-outputs | python3 -c "import json,sys; print(int(json.load(sys.stdin)[0]['geometry']['width']))")
for t in present-center present-side present-widget; do open_window "$t"; done

# They open stacked; drag the topmost first. present-widget: onto the left rail.
# present-side: into the right side zone.
read -r x y < <(center present-widget); super_drag "$x" "$y" 4 "$y"
read -r x y < <(center present-side);   super_drag "$x" "$y" $(( width * 80 / 100 )) "$y"
side_id=$(view present-side "v['id']")
widget_window=$(view present-widget "v['id']")
[[ $(view present-side "v['zone']") != center ]] && pass "side window is in a side zone" || fail "side window setup"
[[ $(view present-widget "v['widgetized']") == True ]] && pass "widget window is widgetized" || fail "widget setup"

# A window in the center zone stays where it is, raised and focused.
before=$(center present-center)
reply=$(ipc scottland/present "{\"window\":$(view present-center "v['id']")}")
sleep 0.5
[[ $reply == *'"window"'* && $(center present-center) == "$before" && $(active) == present-center ]] \
  && pass "center window: stays put and is focused" || fail "center window ($reply; $(center present-center) vs $before; active $(active))"

# A side window flies to the middle and grows to 100%.
reply=$(ipc scottland/present "{\"window\":$side_id}")
sleep 0.6
read -r cx _ < <(center present-side)
[[ $reply == *'"moved"'* && $(view present-side "v['zone']") == center && $(view present-side "v['scale']") == 1.0 \
   && $(( cx - width / 2 )) -le 2 && $(( width / 2 - cx )) -le 2 && $(active) == present-side ]] \
  && pass "side window: in the middle at 100%, focused" \
  || fail "side window ($reply; zone $(view present-side "v['zone']") scale $(view present-side "v['scale']") x $cx; active $(active))"

# A widget, named by its window's id, opens back into its window in the middle.
reply=$(ipc scottland/present "{\"window\":$widget_window}")
sleep 0.8
read -r cx _ < <(center present-widget)
[[ $reply == *'"widget"'* && $(view present-widget "v['widgetized']") == False && $(view present-widget "v['hidden']") == False \
   && $(( cx - width / 2 )) -le 2 && $(( width / 2 - cx )) -le 2 && $(active) == present-widget ]] \
  && pass "widget: opens into its window in the middle, focused" \
  || fail "widget ($reply; widgetized $(view present-widget "v['widgetized']") x $cx; active $(active))"

[[ $(ipc scottland/present '{"window":999999}') == *error* ]] && pass "unknown window: error" || fail "unknown window"

echo "$fails failed"
exit $(( fails > 0 ))
