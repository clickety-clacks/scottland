#!/bin/bash
# End-to-end test of rail widgets (docs/widgets.md) in a headless Scottland with the widget
# service on a private bus. Real input (stipc), real widget programs, real D-Bus.
#
#   tests/widgets-test.sh        prints PASS/FAIL per check; exit status 1 if any failed
set -uo pipefail
cd "$(dirname -- "$0")/.."
fails=0
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; fails=$((fails + 1)); }
check() { local name=$1; shift; if "$@"; then pass "$name"; else fail "$name"; fi; }

h()    { tests/headless.sh ipc "$@" >/dev/null; }
ipc()  { tests/headless.sh ipc "$@"; }
views() { ipc scottland/layout-state; }
view_field() {  # view_field <title-substring> <python expr over v (view) and f (frame)>
  views | python3 -c "
import json,sys
for v in json.load(sys.stdin)['views']:
    if sys.argv[1] in v['title'] or sys.argv[1] == v.get('app_id'):
        f = v.get('frame', {})
        print($2); break" "$1"
}
widget_id() { ipc scottland/widgets | python3 -c "import json,sys; w=json.load(sys.stdin)['widgets']; print(w[0]['id'] if w else '')"; }
bus() { tests/headless.sh run busctl --user "$@"; }
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
}

# A test widget that never shows a window (for the launch timeout), found via SCOTTLAND_WIDGET_PATH.
test_widgets=$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/scottland-test-widgets.XXXXXX")
mkdir -p "$test_widgets/sleeper"
printf 'id = "sleeper"\napps = ["^scottland-test-sleeper$"]\nexec = "sleep 120"\n' >"$test_widgets/sleeper/widget.toml"
export SCOTTLAND_WIDGET_PATH=$test_widgets

tests/headless.sh stop >/dev/null 2>&1
tests/headless.sh start --widgets >/dev/null || { echo "couldn't start headless Scottland"; exit 1; }
monitor_pid=
cleanup() {
  [ -n "$monitor_pid" ] && kill "$monitor_pid" 2>/dev/null
  cp "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/scottland-headless/wayfire.log" \
    "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/scottland-widgets-test.log" 2>/dev/null  # kept for a look
  tests/headless.sh stop >/dev/null 2>&1
  rm -rf "$test_widgets"
}
trap cleanup EXIT
display=$(cat "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/scottland-headless/display")
state_dir=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/scottland/widgets/$display
signals=$(mktemp "${XDG_RUNTIME_DIR:-/tmp}/scottland-test-signals.XXXXXX")
tests/headless.sh run gdbus monitor --session --dest org.scottland.Widgets >"$signals" 2>&1 &
monitor_pid=$!
h wayfire/set-config-options '{"scottland/sounds":false}'
screen_w=$(ipc window-rules/list-outputs | python3 -c "import json,sys; print(int(json.load(sys.stdin)[0]['geometry']['width']))")

# The app: a terminal that, once widgetized, publishes data for its widget (WG11) as itself.
mailbox_script='sleep 6; busctl --user call org.scottland.Widgets /org/scottland/Widgets org.scottland.WidgetData Publish s "{\"unread\": 4}"; busctl --user call org.scottland.Widgets /org/scottland/Widgets org.scottland.Windows GetState > "$XDG_RUNTIME_DIR/scottland-widgets-test-state.txt"; exec sleep 3600'
(tests/headless.sh run foot -T widget-app -W 50x12 sh -c "$mailbox_script" >/dev/null 2>&1 &)
sleep 2
read -r ax ay aw ah <<<"$(view_field widget-app "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
[ -n "${ax:-}" ] || { echo "app window didn't appear"; exit 1; }

# WG1: drag it onto the right rail.
drop_x=$((screen_w - 8)); drop_y=$((ay + ah / 2))
super_drag $((ax + aw / 2)) $((ay + ah / 2)) $drop_x $drop_y
sleep 2.5
check "WG1 the window became a widget (app window hidden)" \
  [ "$(view_field widget-app "v['hidden'] and v['widgetized']")" = True ]
check "WG10 the default card appeared" \
  [ "$(views | python3 -c "import json,sys; print(any(v['widget'] for v in json.load(sys.stdin)['views']))")" = True ]
check "WG4 the widget is at 100%" \
  [ "$(views | python3 -c "import json,sys; print([round(v['applied_scale'],2) for v in json.load(sys.stdin)['views'] if v['widget']][0])")" = 1.0 ]
check "WG4 the widget is placed at the drop point, wholly on screen" \
  python3 -c "
import json,subprocess,sys
v=[v for v in json.loads(subprocess.run(['tests/headless.sh','ipc','scottland/layout-state'],capture_output=True,text=True).stdout)['views'] if v['widget']][0]
f=v['frame']; halo=21.3  # the halo at its widest (SWOLLEN)
ok = f['x'] >= halo and f['x'] + f['width'] + halo <= $screen_w + 1 and abs((f['y'] + f['height']/2) - $drop_y) < 6 and f['x'] + f['width'] >= $screen_w - 40
sys.exit(0 if ok else 1)"
tests/headless.sh run grim /tmp/scottland-widgets-test.png 2>/dev/null

id=$(widget_id)
check "WG9 the widget's D-Bus object exists" \
  bash -c "tests/headless.sh run busctl --user introspect org.scottland.Widgets /org/scottland/widget/$id org.scottland.Widget >/dev/null"
check "WG9 its AppId property is the app's" \
  [ "$(bus get-property org.scottland.Widgets "/org/scottland/widget/$id" org.scottland.Widget AppId)" = 's "foot"' ]
check "WG9 its Title property is the window title" \
  [ "$(bus get-property org.scottland.Widgets "/org/scottland/widget/$id" org.scottland.Widget Title)" = 's "widget-app"' ]

# WG10: a badge announced the standard way (Unity launcher API, per .desktop id).
desktop=$(python3 -c "import json; print(json.load(open('$state_dir/$id.launch.json')).get('desktop',''))")
bus emit /com/canonical/unity/launcherentry/1 com.canonical.Unity.LauncherEntry Update "sa{sv}" \
  "application://${desktop:-foot}.desktop" 2 count x 7 count-visible b true
sleep 0.8
check "WG10 a launcher badge reaches the widget (Badge property)" \
  [ "$(bus get-property org.scottland.Widgets "/org/scottland/widget/$id" org.scottland.Widget Badge)" = "x 7" ]
check "WG10 ...and its state file (what the card shows)" \
  [ "$(python3 -c "import json; print(json.load(open('$state_dir/$id.json'))['badge'])")" = 7 ]
bus emit /com/canonical/unity/launcherentry/1 com.canonical.Unity.LauncherEntry Update "sa{sv}" \
  "application://${desktop:-foot}.desktop" 1 progress d 0.5
sleep 0.5
check "WG10 a progress-only update keeps the badge" \
  [ "$(bus get-property org.scottland.Widgets "/org/scottland/widget/$id" org.scottland.Widget Badge)" = "x 7" ]

# WG11: the app's published data (sent from inside the app's own process tree, above).
sleep 4
check "WG11 data the app published reaches its widget" \
  [ "$(bus get-property org.scottland.Widgets "/org/scottland/widget/$id" org.scottland.Widget Data)" = 's "{\"unread\": 4}"' ]
check "WG12 the app learns it's widgetized (GetState from its own process tree)" \
  grep -q "^bd true " "$XDG_RUNTIME_DIR/scottland-widgets-test-state.txt"
check "WG11 a process that isn't the app can't publish for it" \
  bash -c "! tests/headless.sh run busctl --user call org.scottland.Widgets /org/scottland/Widgets org.scottland.WidgetData Publish s '{}' 2>/dev/null"
check "WG11 a process that isn't a widget can't send as one" \
  bash -c "! tests/headless.sh run busctl --user call org.scottland.Widgets /org/scottland/Widgets org.scottland.WidgetData Send s '{}' 2>/dev/null"
app_pid=$(view_field widget-app "v.get('pid', 0)")
[ -n "$app_pid" ] && [ "$app_pid" != 0 ] || app_pid=$(ipc window-rules/list-views | python3 -c "import json,sys; print([v['pid'] for v in json.load(sys.stdin) if v['title']=='widget-app'][0])")
check "WG12 StateChanged told the app it's widgetized" grep -q "StateChanged (uint32 $app_pid, true" "$signals"

# WG4/WG1: sliding the widget along its rail keeps it a widget, at 100%, on screen.
read -r wx wy ww wh <<<"$(views | python3 -c "
import json,sys
v=[v for v in json.load(sys.stdin)['views'] if v['widget']][0]; f=v['frame']
print(round(f['x']), round(f['y']), round(f['width']), round(f['height']))")"
super_drag $((wx + ww / 2)) $((wy + wh / 2)) $((wx + ww / 2 + 40)) $((wy + wh / 2 - 150))
sleep 1.5
check "WG1 sliding a widget along its rail keeps it a widget" \
  [ "$(view_field widget-app "v['hidden'] and v['widgetized']")" = True ]
check "WG4 ...still at 100%, wholly on screen, halo included, where it was dropped (vertically)" \
  python3 -c "
import json,subprocess,sys
v=[v for v in json.loads(subprocess.run(['tests/headless.sh','ipc','scottland/layout-state'],capture_output=True,text=True).stdout)['views'] if v['widget']][0]
f=v['frame']; ok = round(v['applied_scale'],2) == 1.0 and f['x'] >= 21.3 and f['x'] + f['width'] + 21.3 <= $screen_w + 1 and abs(f['y'] + f['height']/2 - ($wy + $wh / 2 - 150)) < 6
sys.exit(0 if ok else 1)"

# WG5: dragging the widget off the rail restores the window there; the widget goes.
read -r wx wy ww wh <<<"$(views | python3 -c "
import json,sys
v=[v for v in json.load(sys.stdin)['views'] if v['widget']][0]; f=v['frame']
print(round(f['x']), round(f['y']), round(f['width']), round(f['height']))")"
restore_x=$((screen_w * 3 / 4))
super_drag $((wx + ww / 2)) $((wy + wh / 2)) $restore_x 300
sleep 1.5
check "WG5 dragging the widget off the rail restores the window" \
  [ "$(view_field widget-app "not v['hidden'] and not v['widgetized']")" = True ]
check "WG5 ...where it was dropped" \
  python3 -c "
import json,subprocess,sys
v=[v for v in json.loads(subprocess.run(['tests/headless.sh','ipc','scottland/layout-state'],capture_output=True,text=True).stdout)['views'] if v['title']=='widget-app'][0]
f=v['frame']; sys.exit(0 if abs(f['x']+f['width']/2 - $restore_x) < 30 and abs(f['y']+f['height']/2 - 300) < 30 else 1)"
check "WG5 ...and the widget is gone (dismissed, not closed)" \
  [ "$(views | python3 -c "import json,sys; print(sum(1 for v in json.load(sys.stdin)['views'] if v['widget']))")" = 0 ]
check "WG12 StateChanged told the app it's back (not widgetized)" grep -q "StateChanged (uint32 $app_pid, false" "$signals"
check "WG9 the widget's state files are gone" bash -c "! ls '$state_dir'/$id.* 2>/dev/null | grep -q ."

# WG5: closing the widget closes the app's window.
read -r ax ay aw ah <<<"$(view_field widget-app "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
super_drag $((ax + aw / 2)) $((ay + ah / 2)) $((screen_w - 8)) $((ay + ah / 2))
sleep 2.5
id=$(widget_id)
bus call org.scottland.Widgets "/org/scottland/widget/$id" org.scottland.Widget Close >/dev/null 2>&1
sleep 2
check "WG5 closing the widget closes the app's window too" \
  [ "$(views | python3 -c "import json,sys; print(len(json.load(sys.stdin)['views']))")" = 0 ]

# WG5: the widget's own window closing (its program exits) closes the app's window too.
(tests/headless.sh run foot -T widget-app3 -W 40x10 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
read -r ax ay aw ah <<<"$(view_field widget-app3 "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
super_drag $((ax + aw / 2)) $((ay + ah / 2)) $((screen_w - 8)) $((ay + ah / 2))
sleep 2.5
widget_view=$(ipc scottland/widgets | python3 -c "import json,sys; print(json.load(sys.stdin)['widgets'][0]['widget_view'])")
h window-rules/close-view "{\"id\": $widget_view}"
sleep 2
check "WG5 the widget's window closing closes the app's window" \
  [ "$(views | python3 -c "import json,sys; print(len(json.load(sys.stdin)['views']))")" = 0 ]

# WG5: a widget whose window never appears is abandoned: the app's window comes back and the
# widget's process is ended.
(tests/headless.sh run foot --app-id scottland-test-sleeper -T widget-app4 -W 40x10 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
read -r ax ay aw ah <<<"$(view_field widget-app4 "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
super_drag $((ax + aw / 2)) $((ay + ah / 2)) $((screen_w - 8)) $((ay + ah / 2))
sleep 1
sleeper=$(ipc scottland/widgets | python3 -c "import json,sys; w=json.load(sys.stdin)['widgets']; print(w[0]['widget_pid'] if w else '')")
check "WG5 (timeout) the widget launched and the app's window is hidden" \
  [ "$(view_field widget-app4 "v['hidden']")" = True ]
sleep 9
check "WG5 (timeout) the app's window is restored after 8 s" \
  [ "$(view_field widget-app4 "not v['hidden'] and not v['widgetized']")" = True ]
check "WG5 (timeout) the widget's process was ended" bash -c "[ -n '$sleeper' ] && ! kill -0 '$sleeper' 2>/dev/null"
h window-rules/close-view "{\"id\": $(view_field widget-app4 "v['id']")}"
sleep 1

# WG5: unloading the plugin (a reload) gives the app its window back and ends the widget; the
# widget service drops its objects.
(tests/headless.sh run foot -T widget-app5 -W 40x10 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
read -r ax ay aw ah <<<"$(view_field widget-app5 "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
super_drag $((ax + aw / 2)) $((ay + ah / 2)) $((screen_w - 8)) $((ay + ah / 2))
sleep 2.5
id=$(widget_id)
plugins=$(ipc wayfire/get-config-option '{"option":"core/plugins"}' | python3 -c "import json,sys; print(json.load(sys.stdin)['value'])")
without=$(python3 -c "import sys; print(' '.join(p for p in sys.argv[1].split() if p != 'scottland'))" "$plugins")
h wayfire/set-config-options "$(python3 -c "import json,sys; print(json.dumps({'core/plugins': sys.argv[1]}))" "$without")"
sleep 2
check "WG5 (unload) the widget service drops the widget's object" \
  bash -c "! tests/headless.sh run busctl --user tree org.scottland.Widgets 2>/dev/null | grep -q '/org/scottland/widget/$id'"
check "WG5 (unload) the widget's window closes" \
  [ "$(ipc window-rules/list-views | python3 -c "import json,sys; print(sum(1 for v in json.load(sys.stdin) if v['title'].startswith('Scottland widget')))")" = 0 ]
h wayfire/set-config-options "$(python3 -c "import json,sys; print(json.dumps({'core/plugins': sys.argv[1]}))" "$plugins")"
sleep 2
check "WG5 (reload) the app's window is back, not widgetized" \
  [ "$(view_field widget-app5 "not v['hidden'] and not v['widgetized']")" = True ]
h window-rules/close-view "{\"id\": $(view_field widget-app5 "v['id']")}"
sleep 1

# WG5: the app's window closing takes the widget with it.
(tests/headless.sh run foot -T widget-app2 -W 40x10 sh -c 'sleep 5; exit 0' >/dev/null 2>&1 &)
sleep 1.5
read -r ax ay aw ah <<<"$(view_field widget-app2 "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
super_drag $((ax + aw / 2)) $((ay + ah / 2)) $((screen_w - 8)) $((ay + ah / 2))
sleep 5
check "WG5 the app's window closing closes its widget" \
  [ "$(views | python3 -c "import json,sys; print(len(json.load(sys.stdin)['views']))")" = 0 ]

echo
[ "$fails" -eq 0 ] && echo "all widget checks passed" || echo "$fails check(s) failed"
exit $((fails > 0))
