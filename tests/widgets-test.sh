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
headless_dir=${SCOTTLAND_HEADLESS_DIR:-$PWD/build/headless}
artifacts=$headless_dir.results
mkdir -p "$artifacts" build
test_widgets=$(mktemp -d "${TMPDIR:-$PWD/build}/scottland-test-widgets.XXXXXX")
mkdir -p "$test_widgets/sleeper" "$test_widgets/sender" "$test_widgets/daemon" "$test_widgets/stubborn" "$test_widgets/return"
cp -a tests/widgets/gravity "$test_widgets/gravity"
cp -a tests/widgets/return/. "$test_widgets/return/"
# Shows a window that refuses to close when asked.
cat >"$test_widgets/stubborn/widget.toml" <<'TOML'
id = "stubborn"
apps = ["^scottland-test-stubborn$"]
exec = "python3 ./widget.py"
TOML
cat >"$test_widgets/stubborn/widget.py" <<'PY'
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk
app = Gtk.Application(application_id="org.scottland.TestStubborn")
def activate(application):
    window = Gtk.ApplicationWindow(application=application, title="stubborn-widget")
    window.set_default_size(260, 90)
    window.connect("close-request", lambda *_: True)  # refuse
    window.present()
app.connect("activate", activate)
app.run()
PY
# Forks its window off and exits at once; gets the app's title (with "$" in it) as an argument.
cat >"$test_widgets/daemon/widget.toml" <<'TOML'
id = "daemon"
apps = ["^scottland-test-daemon$"]
exec = "./start %t"
TOML
printf '#!/bin/sh\nsetsid -f foot -T "$1" sh -c "exec sleep 600"\nsleep 3\nexit 0\n' >"$test_widgets/daemon/start"
chmod +x "$test_widgets/daemon/start"
# Never shows a window, ignores SIGTERM, and leaves a child behind: all of it must still end.
cat >"$test_widgets/sleeper/widget.toml" <<'TOML'
id = "sleeper"
apps = ["^scottland-test-sleeper$"]
exec = 'sh -c "trap \"\" TERM; sleep 121 & wait"'
TOML
# A real widget (a terminal window) that sends its app a message over the mailbox (WG11).
# Its window is a terminal; the message is sent by the widget's launch shell, a sibling of the
# window's process (a controller beside its renderer).
cat >"$test_widgets/sender/widget.toml" <<'TOML'
id = "sender"
apps = ["^scottland-test-sender$"]
exec = "./start"
TOML
printf '#!/bin/sh\nfoot -T sender-widget sh -c "exec sleep 600" &\nsleep 3\nbusctl --user call org.scottland.Widgets /org/scottland/Widgets org.scottland.WidgetData Send s %s\nwait\n' "'{\"hi\": 1}'" >"$test_widgets/sender/start"
chmod +x "$test_widgets/sender/start"
export SCOTTLAND_WIDGET_PATH=$test_widgets

tests/headless.sh stop >/dev/null 2>&1
tests/headless.sh start --widgets >/dev/null || { echo "couldn't start headless Scottland"; exit 1; }
monitor_pid=
cleanup() {
  [ -n "$monitor_pid" ] && kill "$monitor_pid" 2>/dev/null
  cp "$headless_dir/wayfire.log" "$artifacts/wayfire-final.log" 2>/dev/null
  cp "$headless_dir/state/scottland/widgets.log" "$artifacts/widgets.log" 2>/dev/null
  tests/headless.sh stop >/dev/null 2>&1
  rm -rf "$test_widgets"
  if [[ -n ${src:-} ]]; then rm -rf "$src"; fi
}
trap cleanup EXIT
display=$(cat "$headless_dir/display")
state_dir=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/scottland/widgets/$display
signals=$artifacts/widget-signals.log
tests/headless.sh run gdbus monitor --session --dest org.scottland.Widgets >"$signals" 2>&1 &
monitor_pid=$!
h wayfire/set-config-options '{"scottland/sounds":false}'
screen_w=$(ipc window-rules/list-outputs | python3 -c "import json,sys; print(int(json.load(sys.stdin)[0]['geometry']['width']))")

# Input-edge regressions use this same private session and close only their own windows.
tests/headless.sh run python3 tests/widget-input-test.py --log \
  "$headless_dir/wayfire.log" \
  || fail "widget input regressions"

# The app: a terminal that, once widgetized, publishes data for its widget (WG11) as itself.
mailbox_script='sleep 6; busctl --user call org.scottland.Widgets /org/scottland/Widgets org.scottland.WidgetData Publish s "{\"unread\": 4}"; busctl --user call org.scottland.Widgets /org/scottland/Widgets org.scottland.Windows GetState > "$1"; exec sleep 3600'
(tests/headless.sh run foot -T widget-app -W 50x12 sh -c "$mailbox_script" sh "$artifacts/app-state.txt" >/dev/null 2>&1 &)
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
check "WG4 the widget floats above ordinary windows (always on top)" \
  [ "$(ipc window-rules/list-views | python3 -c "import json,sys; print([v['always-on-top'] for v in json.load(sys.stdin) if v['title'].startswith('Scottland widget')])")" = "[True]" ]
check "WG4 the widget is placed at the drop point, wholly on screen" \
  python3 -c "
import json,subprocess,sys
v=[v for v in json.loads(subprocess.run(['tests/headless.sh','ipc','scottland/layout-state'],capture_output=True,text=True).stdout)['views'] if v['widget']][0]
f=v['frame']; halo=21.3  # the halo at its widest (SWOLLEN)
ok = f['x'] >= halo and f['x'] + f['width'] + halo <= $screen_w + 1 and abs((f['y'] + f['height']/2) - $drop_y) < 6 and f['x'] + f['width'] >= $screen_w - 40
sys.exit(0 if ok else 1)"
tests/headless.sh run grim "$artifacts/widget-placement.png" 2>/dev/null

id=$(widget_id)
check "WG9 the widget's D-Bus object exists" \
  bash -c "tests/headless.sh run busctl --user introspect org.scottland.Widgets /org/scottland/widget/$id org.scottland.Widget >/dev/null"
check "WG9 its AppId property is the app's" \
  [ "$(bus get-property org.scottland.Widgets "/org/scottland/widget/$id" org.scottland.Widget AppId)" = 's "foot"' ]
check "WG9 its Title property is the window title" \
  [ "$(bus get-property org.scottland.Widgets "/org/scottland/widget/$id" org.scottland.Widget Title)" = 's "widget-app"' ]

# WG10: a badge announced the standard way (Unity launcher API, per .desktop id).
unit=$(ipc scottland/widgets | python3 -c "import json,sys; print(json.load(sys.stdin)['widgets'][0]['widget_unit'])")
desktop=$(python3 -c "import json; print(json.load(open('$state_dir/$unit.json')).get('desktop',''))")
bus emit /com/canonical/unity/launcherentry/1 com.canonical.Unity.LauncherEntry Update "sa{sv}" \
  "application://${desktop:-foot}.desktop" 2 count x 7 count-visible b true
sleep 0.8
check "WG10 a launcher badge reaches the widget (Badge property)" \
  [ "$(bus get-property org.scottland.Widgets "/org/scottland/widget/$id" org.scottland.Widget Badge)" = "x 7" ]
check "WG10 ...and its state file (what the card shows)" \
  [ "$(python3 -c "import json; print(json.load(open('$state_dir/$unit.json'))['badge'])")" = 7 ]
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
  grep -q "^bd true " "$artifacts/app-state.txt"
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
check "WG13 the window dropped from a widget drag is at its size at once (no growing from the rail's)" \
  [ "$(view_field widget-app "abs(v['applied_scale'] - v['target_scale']) < 0.01 and not v['hidden']")" = True ]
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
check "WG9 the widget's state files are gone" bash -c "! ls '$state_dir'/$unit.json 2>/dev/null | grep -q ."

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
unit=$(ipc scottland/widgets | python3 -c "import json,sys; w=json.load(sys.stdin)['widgets']; print(w[0]['widget_unit'] if w else '')")
procs=$(cat "/sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/app.slice/$unit/cgroup.procs" 2>/dev/null | tr '\n' ' ')
check "WG5 (timeout) the widget runs in its own scope, with its child" [ "$(echo $procs | wc -w)" -ge 2 ]
check "WG11 a widget with no window yet has no window process (widget_pid 0)" \
  [ "$(ipc scottland/widgets | python3 -c "import json,sys; print(json.load(sys.stdin)['widgets'][0]['widget_pid'])")" = 0 ]
check "WG5/WG22 (timeout) the app stays visible until a card can take its image" \
  [ "$(view_field widget-app4 "not v['hidden'] and v['widgetized']")" = True ]
sleep 11
check "WG5 (timeout) the app's window is restored after 8 s" \
  [ "$(view_field widget-app4 "not v['hidden'] and not v['widgetized']")" = True ]
check "WG5 (timeout) every process of the widget was ended (it ignored SIGTERM; its child too)" \
  bash -c "for p in $procs; do kill -0 \$p 2>/dev/null && exit 1; done; ! systemctl --user is-active --quiet '$unit'"
h window-rules/close-view "{\"id\": $(view_field widget-app4 "v['id']")}"
sleep 1

# WG2/WG8: a widget whose launcher forks its window off and exits is still adopted (by its scope),
# and its arguments reach it literally ("$" isn't expanded on the way).
weird='${HOME} $$ two words'
(tests/headless.sh run foot --app-id scottland-test-daemon -T "$weird" -W 40x10 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
read -r ax ay aw ah <<<"$(views | python3 -c "
import json,sys
v=[v for v in json.load(sys.stdin)['views'] if v.get('app_id')=='scottland-test-daemon' and not v['widget']][0]; f=v['frame']
print(round(f['x']), round(f['y']), round(f['width']), round(f['height']))")"
super_drag $((ax + aw / 2)) $((ay + ah / 2)) $((screen_w - 8)) $((ay + ah / 2))
check "DM2 launcher exit is published as a newer full snapshot" \
  tests/headless.sh run python3 tests/model-process-test.py
sleep 3.5
check "WG2 a widget that forks its window off and exits is adopted (placed, at 100%)" \
  python3 -c "
import json,subprocess,sys
w=[v for v in json.loads(subprocess.run(['tests/headless.sh','ipc','scottland/layout-state'],capture_output=True,text=True).stdout)['views'] if v['widget']]
f=w[0]['frame'] if w else {}
sys.exit(0 if len(w)==1 and round(w[0]['applied_scale'],2)==1.0 and f['x'] + f['width'] >= $screen_w - 40 else 1)"
check "WG8 its argument arrived literally (\$ not expanded)" \
  python3 -c "
import json,subprocess,sys
w=[v for v in json.loads(subprocess.run(['tests/headless.sh','ipc','scottland/layout-state'],capture_output=True,text=True).stdout)['views'] if v['widget']]
sys.exit(0 if w and w[0]['title']==sys.argv[1] else 1)" "$weird"
h window-rules/close-view "{\"id\": $(views | python3 -c "import json,sys; print([v['id'] for v in json.load(sys.stdin)['views'] if v['widget']][0])")}"
sleep 2
check "WG5 ...and closing it closes the app" \
  [ "$(views | python3 -c "import json,sys; print(sum(1 for v in json.load(sys.stdin)['views'] if v.get('app_id')=='scottland-test-daemon'))")" = 0 ]

# WG9/WG11: a real widget sends its app a message; live title; Restore() over D-Bus.
(tests/headless.sh run foot --app-id scottland-test-sender -T sender-app -W 40x10 sh -c 'sleep 6; printf "\033]2;sender-renamed\007"; exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
read -r ax ay aw ah <<<"$(view_field sender-app "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
sender_pid=$(ipc window-rules/list-views | python3 -c "import json,sys; print([v['pid'] for v in json.load(sys.stdin) if v['title']=='sender-app'][0])")
super_drag $((ax + aw / 2)) $((ay + ah / 2)) $((screen_w - 8)) $((ay + ah / 2))
sleep 5
id=$(widget_id)
check "WG11 a widget's Send (from a process beside its window's) reaches its app" \
  grep -q "Received (uint32 $sender_pid, uint64 $id, '{\"hi\": 1}')" "$signals"
sleep 2
check "WG9 the Title property follows the app's title live" \
  [ "$(bus get-property org.scottland.Widgets "/org/scottland/widget/$id" org.scottland.Widget Title)" = 's "sender-renamed"' ]
check "WG9 ...with a PropertiesChanged signal" grep -q "sender-renamed" "$signals"
bus call org.scottland.Widgets "/org/scottland/widget/$id" org.scottland.Widget Restore >/dev/null 2>&1
sleep 1.5
check "WG9 Restore() brings the app's window back and the widget goes" \
  [ "$(view_field sender-renamed "not v['hidden'] and not v['widgetized']")/$(views | python3 -c "import json,sys; print(sum(1 for v in json.load(sys.stdin)['views'] if v['widget']))")" = True/0 ]
h window-rules/close-view "{\"id\": $(view_field sender-renamed "v['id']")}"
sleep 1

# WG12: an app with two windows (one foot server); GetState, asked from the app's own process
# tree, reports the first window's target scale, then, once that window closes while the
# other keeps focus, the remaining window's.
(tests/headless.sh run foot --server >/dev/null 2>&1 &)
sleep 1
ask=$artifacts/scottland-widgets-test-ask
rm -f "$ask.go1" "$ask.go2" "$ask.answer1" "$ask.answer2"
(tests/headless.sh run footclient -T two-a -W 40x10 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1
read -r ax ay aw ah <<<"$(view_field two-a "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
super_drag $((ax + aw / 2)) $((ay + ah / 2)) $((screen_w * 4 / 5)) $((ay + ah / 2))
sleep 1
(tests/headless.sh run footclient -T two-b -W 40x10 sh -c "for n in 1 2; do while [ ! -e $ask.go\$n ]; do sleep 0.2; done; busctl --user call org.scottland.Widgets /org/scottland/Widgets org.scottland.Windows GetState > $ask.answer\$n; done; exec sleep 3600" >/dev/null 2>&1 &)
sleep 1
read -r bx by bw bh <<<"$(view_field two-b "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
h stipc/move_cursor "{\"x\":$((bx + bw / 2)),\"y\":$((by + bh / 2))}"; h stipc/feed_button '{"combo":"BTN_LEFT","mode":"full"}'
sleep 0.5
target_a=$(view_field two-a "round(v['target_scale'], 3)")
touch "$ask.go1"; sleep 1.5
check "WG12 a two-window app: GetState gives the first window's target scale ($target_a)" \
  python3 -c "import sys; w=open('$ask.answer1').read().split(); sys.exit(0 if w[1]=='false' and abs(float(w[2]) - $target_a) < 0.005 and $target_a < 0.99 else 1)"
h window-rules/close-view "{\"id\": $(view_field two-a "v['id']")}"
sleep 1
touch "$ask.go2"; sleep 1.5
check "WG12 ...after the first window closes (focus unchanged), the remaining window's 100%" \
  grep -q "^bd false 1$" "$ask.answer2"
h window-rules/close-view "{\"id\": $(view_field two-b "v['id']")}"
rm -f "$ask.go1" "$ask.go2" "$ask.answer1" "$ask.answer2"
sleep 1

# A reload after an update replaces a widget service running older code (and keeps a current one).
hooks_dir=$(tests/headless.sh run sh -c 'echo $SCOTTLAND_HOOKS')
bus_pid_file=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/scottland/$display.widget-bus.pid
old_bus=$(sed -n 1p "$bus_pid_file")
tests/headless.sh run "$hooks_dir/reload.d/08-widget-bus"; sleep 1
check "reload keeps a widget service that runs the installed code" [ "$(sed -n 1p "$bus_pid_file")" = "$old_bus" ]
printf '%s\n%s\n' "$old_bus" "fingerprint-of-older-code" >"$bus_pid_file"  # as if it predated an update
tests/headless.sh run "$hooks_dir/reload.d/08-widget-bus"; sleep 2
new_bus=$(sed -n 1p "$bus_pid_file")
check "reload replaces a widget service older than the installed code" \
  bash -c "[ '$new_bus' != '$old_bus' ] && ! kill -0 '$old_bus' 2>/dev/null && kill -0 '$new_bus' && tests/headless.sh run busctl --user status org.scottland.Widgets >/dev/null"

# WG10: the session's palette file (what the card's colors follow).
palette=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/scottland/$display.palette.json
tests/headless.sh run "$hooks_dir/libexec/scottland-color-scheme" ensure; sleep 2
watcher=$(sed -n 1p "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/scottland/$display.color-scheme.pid")
check "WG10 the session's palette file has every color" \
  python3 -c "import json,re,sys; p=json.load(open('$palette')); sys.exit(0 if p['scheme'] in ('light','dark') and all(re.fullmatch('#[0-9a-fA-F]{6}', p[k]) for k in ('background','foreground','muted','accent','alert')) else 1)"
rm -f "$palette"

# WG5: an app that hides its window while widgetized takes its widget away; shown again, the
# window is an ordinary, visible window.
(tests/headless.sh run env HIDE_AT=9 python3 tests/remap-app.py >/dev/null 2>&1 &)
sleep 1.5
read -r ax ay aw ah <<<"$(view_field remap-app "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
super_drag $((ax + aw / 2)) $((ay + ah / 2)) $((screen_w - 8)) $((ay + ah / 2))
sleep 2
check "WG5 (hide) the app's window became a widget" [ "$(view_field remap-app "v['hidden'] and v['widgetized']")" = True ]
sleep 7  # it hides itself at 9 s and shows itself again at 10 s
check "WG5 (hide) shown again, the app's window is visible and not widgetized, and the widget is gone" \
  [ "$(view_field remap-app "not v['hidden'] and not v['widgetized']")/$(views | python3 -c "import json,sys; print(sum(1 for v in json.load(sys.stdin)['views'] if v['widget']))")" = True/0 ]
h window-rules/close-view "{\"id\": $(view_field remap-app "v['id']")}"
sleep 1

# WG5: the close dot on a widget that refuses to close: the app's window closes, and the widget
# is ended 3 s later.
(tests/headless.sh run foot --app-id scottland-test-stubborn -T stubborn-app -W 40x10 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
read -r ax ay aw ah <<<"$(view_field stubborn-app "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
super_drag $((ax + aw / 2)) $((ay + ah / 2)) $((screen_w - 8)) $((ay + ah / 2))
sleep 4
read -r dx dy <<<"$(views | python3 -c "
import json,sys
v=[v for v in json.load(sys.stdin)['views'] if v['widget']][0]; f=v['frame']
print(round(f['x'] + f['width']/2), round(f['y'] + f['height'] + f['thickness']/2))")"
h stipc/move_cursor "{\"x\":$dx,\"y\":$dy}"; sleep 0.2
h stipc/feed_button '{"combo":"BTN_LEFT","mode":"full"}'
sleep 1
check "WG5 (close dot) the app's window closes with the widget" \
  [ "$(views | python3 -c "import json,sys; print(sum(1 for v in json.load(sys.stdin)['views'] if v['title']=='stubborn-app'))")" = 0 ]
sleep 5
check "WG5 (close dot) the widget that refused to close is ended" \
  [ "$(views | python3 -c "import json,sys; print(sum(1 for v in json.load(sys.stdin)['views'] if v['title']=='stubborn-widget'))")" = 0 ]

# WG13: a live morph while dragging. Held on the rail, the window is shown as its widget (frame
# reshaped, contents cross-faded) and the widget itself stays unseen; dragged back out, it's the
# window again; let go there, it stays a window and no widget is left.
(tests/headless.sh run foot -T morph-app -W 50x12 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
read -r ax ay aw ah <<<"$(view_field morph-app "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
mx=$((ax + aw / 2)); my=$((ay + ah / 2))
glide() { for i in $(seq 1 12); do h stipc/move_cursor "{\"x\":$(( $1 + ($3 - $1) * i / 12 )),\"y\":$(( $2 + ($4 - $2) * i / 12 ))}"; sleep 0.03; done; }
h stipc/move_cursor "{\"x\":$mx,\"y\":$my}"; sleep 0.2
h stipc/feed_key '{"key":"KEY_LEFTMETA","state":true}'
h stipc/feed_button '{"combo":"BTN_LEFT","mode":"press"}'
glide $mx $my $((screen_w - 8)) $my
sleep 1.5
check "WG13 held on the rail, the window shows as its widget (shape and contents)" \
  [ "$(view_field morph-app "(f.get('morph') or {}).get('shape', 0) > 0.99 and (f.get('morph') or {}).get('fade', 0) > 0.99")" = True ]
check "WG13 ...and is drawn at the widget's size" \
  python3 -c "
import json,subprocess,sys
vs=json.loads(subprocess.run(['tests/headless.sh','ipc','scottland/layout-state'],capture_output=True,text=True).stdout)['views']
a=[v for v in vs if v['title']=='morph-app'][0]['frame']; w=[v for v in vs if v['widget']][0]['frame']
sys.exit(0 if abs(a['width']-w['width']) < 2 and abs(a['height']-w['height']) < 2 else 1)"
check "WG13 ...while the widget itself stays unseen" \
  [ "$(views | python3 -c "import json,sys; print([v['hidden'] for v in json.load(sys.stdin)['views'] if v['widget']])")" = "[True]" ]
glide $((screen_w - 8)) $my $((screen_w / 2)) $my
sleep 1
check "WG13 dragged back out, it's the window again" \
  [ "$(view_field morph-app "not f.get('morph')")" = True ]
h stipc/feed_button '{"combo":"BTN_LEFT","mode":"release"}'
h stipc/feed_key '{"key":"KEY_LEFTMETA","state":false}'
sleep 1.5
check "WG13 let go off the rail: still a window, and the widget it previewed is gone" \
  [ "$(view_field morph-app "not v['hidden'] and not v['widgetized']")/$(views | python3 -c "import json,sys; print(sum(1 for v in json.load(sys.stdin)['views'] if v['widget']))")" = True/0 ]

# WG14: Esc cancels a drag: back where it was picked up, in its original form.
read -r ax ay aw ah <<<"$(view_field morph-app "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
mx=$((ax + aw / 2)); my=$((ay + ah / 2))
origin=$(view_field morph-app "(round(f['x']), round(f['y']))")
h stipc/move_cursor "{\"x\":$mx,\"y\":$my}"; sleep 0.2
h stipc/feed_key '{"key":"KEY_LEFTMETA","state":true}'
h stipc/feed_button '{"combo":"BTN_LEFT","mode":"press"}'
glide $mx $my $((screen_w - 8)) $my
sleep 1.2
h stipc/feed_key '{"key":"KEY_ESC","state":true}'; h stipc/feed_key '{"key":"KEY_ESC","state":false}'
sleep 1
h stipc/feed_button '{"combo":"BTN_LEFT","mode":"release"}'
h stipc/feed_key '{"key":"KEY_LEFTMETA","state":false}'
sleep 1
check "WG14 Esc (after it turned into a widget): back where it started, a window, no widget" \
  [ "$(view_field morph-app "(round(f['x']), round(f['y']))")/$(view_field morph-app "not v['hidden'] and not v['widgetized'] and not f.get('morph')")/$(views | python3 -c "import json,sys; print(sum(1 for v in json.load(sys.stdin)['views'] if v['widget']))")" = "$origin/True/0" ]
h window-rules/close-view "{\"id\": $(view_field morph-app "v['id']")}"
sleep 1

# WG15: attention. A widgetized app that rings its bell (an activation request), or sends a
# desktop notification from its own process, gets attention on its widget; going to the widget
# answers it. An ordinary window's bell doesn't take focus.
# Each app goes to the rail as it opens (they open in the same place). The bell and the
# notification come after the last widget has taken focus (an app whose widget you're on has no
# news for you).
(tests/headless.sh run foot -o bell.urgent=yes -T bell-app -W 40x8 sh -c 'sleep 10; printf "\a"; exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
read -r ax ay aw ah <<<"$(view_field bell-app "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
super_drag $((ax + aw / 2)) $((ay + ah / 2)) $((screen_w - 8)) $((ay + ah / 2))
sleep 1.5
(tests/headless.sh run env NOTIFY_AT=7 python3 tests/notify-app.py >/dev/null 2>&1 &)
sleep 1.5
read -r ax ay aw ah <<<"$(view_field notify-app "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
super_drag $((ax + aw / 2)) $((ay + ah / 2)) $((screen_w - 8)) $((ay - 150))
sleep 1.5
(tests/headless.sh run foot -o bell.urgent=yes -T plain-bell -W 40x8 sh -c 'sleep 4; printf "\a"; exec sleep 3600' >/dev/null 2>&1 &)
sleep 1
(tests/headless.sh run foot -T focus-taker -W 30x6 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)  # plain-bell rings from behind it
sleep 1
focused_before=$(ipc window-rules/get-focused-view | python3 -c "import json,sys; print(json.load(sys.stdin)['info']['id'])")
sleep 6
attention_of() { views | python3 -c "
import json,sys
w=[v for v in json.load(sys.stdin)['views'] if v['widget'] and v['title'].endswith(sys.argv[1])]
print(bool(w and w[0]['frame'].get('attention')))" "$1"; }
check "WG15 a widgetized app's bell shows attention on its widget" [ "$(attention_of bell-app)" = True ]
check "WG15 a widgetized app's desktop notification shows attention on its widget" [ "$(attention_of notify-app)" = True ]
check "WG15 ...also reported as Urgent" \
  [ "$(ipc scottland/widgets | python3 -c "import json,sys; print(all(w['urgent'] for w in json.load(sys.stdin)['widgets']))")" = True ]
check "WG15/L28 a window in the background ringing its bell doesn't take focus" \
  [ "$(ipc window-rules/get-focused-view | python3 -c "import json,sys; print(json.load(sys.stdin)['info']['title'])")" = focus-taker ]
check "WG15 ...its own halo shows the attention" [ "$(view_field plain-bell "bool(f.get('attention'))")" = True ]
bell_window=$(views | python3 -c "import json,sys; print([v['id'] for v in json.load(sys.stdin)['views'] if v['title'].endswith('bell-app') and not v['widget']][0])")
h scottland/attention "{\"window\": $bell_window, \"attention\": false}"
sleep 0.3
check "WG15 an integration taking back its attention leaves the app's own (the bell's)" [ "$(attention_of bell-app)" = True ]
read -r wx wy ww wh <<<"$(views | python3 -c "
import json,sys
v=[v for v in json.load(sys.stdin)['views'] if v['widget'] and v['title'].endswith('bell-app')][0]; f=v['frame']
print(round(f['x']), round(f['y']), round(f['width']), round(f['height']))")"
h stipc/move_cursor "{\"x\":$((wx + ww / 2)),\"y\":$((wy + wh / 2))}"; h stipc/feed_button '{"combo":"BTN_LEFT","mode":"full"}'
sleep 1
check "WG15 going to the widget answers its attention" [ "$(attention_of bell-app)" = False ]
for t in bell-app notify-app plain-bell focus-taker; do
  id=$(views | python3 -c "
import json,sys
v=[v for v in json.load(sys.stdin)['views'] if v['title']==sys.argv[1] or v['title'].endswith(': '+sys.argv[1])]
print(v[0]['id'] if v else '')" $t)
  [ -n "$id" ] && h window-rules/close-view "{\"id\": $id}"
done
sleep 2

# WG13: clicking a widget's halo (a press and release, no move) leaves it a widget, in place.
(tests/headless.sh run foot -T halo-click -W 40x8 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
read -r ax ay aw ah <<<"$(view_field halo-click "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
super_drag $((ax + aw / 2)) $((ay + ah / 2)) $((screen_w - 8)) $((ay + ah / 2))
sleep 2.5
read -r hx hy before <<<"$(views | python3 -c "
import json,sys
v=[v for v in json.load(sys.stdin)['views'] if v['widget'] and v['title'].endswith('halo-click')][0]; f=v['frame']
print(round(f['x'] + f['width'] / 2), round(f['y'] - f['thickness'] / 2), str(round(f['x'])) + ',' + str(round(f['y'])))")"
h stipc/move_cursor "{\"x\":$hx,\"y\":$hy}"; sleep 0.3
h stipc/feed_button '{"combo":"BTN_LEFT","mode":"full"}'
sleep 1.5
check "WG13 a click on a widget's halo leaves it a widget, where it was" \
  [ "$(views | python3 -c "
import json,sys
w=[v for v in json.load(sys.stdin)['views'] if v['widget'] and v['title'].endswith('halo-click')]
print(str(round(w[0]['frame']['x'])) + ',' + str(round(w[0]['frame']['y'])) if w else 'gone')")/$(view_field halo-click "v['hidden']")" = "$before/True" ]
h window-rules/close-view "{\"id\": $(view_field halo-click "v['id']")}"
sleep 2

# WG17: clicking a default card opens its window in the middle of the screen.
(tests/headless.sh run foot -T open-app -W 40x8 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
read -r ax ay aw ah <<<"$(view_field open-app "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
super_drag $((ax + aw / 2)) $((ay + ah / 2)) $((screen_w - 8)) $((ay + ah / 2))
sleep 2.5
read -r ox oy <<<"$(views | python3 -c "
import json,sys
v=[v for v in json.load(sys.stdin)['views'] if v['widget'] and v['title'].endswith('open-app')][0]; f=v['frame']
print(round(f['x'] + f['width'] / 2), round(f['y'] + f['height'] / 2))")"
h stipc/move_cursor "{\"x\":$ox,\"y\":$oy}"; sleep 0.3
h stipc/feed_button '{"combo":"BTN_LEFT","mode":"full"}'
sleep 1.5
check "WG17 clicking a card restores its remembered center (and the card goes)" \
  python3 -c "
import json,subprocess,sys
vs=json.loads(subprocess.run(['tests/headless.sh','ipc','scottland/layout-state'],capture_output=True,text=True).stdout)['views']
w=[v for v in vs if v['title']=='open-app'][0]; f=w['frame']
cards=[v for v in vs if v['widget'] and v['title'].endswith('open-app')]
sys.exit(0 if not w['hidden'] and abs(f['x']+f['width']/2 - $screen_w/2) < 30 and not cards else 1)"
h window-rules/close-view "{\"id\": $(view_field open-app "v['id']")}"
sleep 1

# WG16: Super+M collapses all widgets to their icons, and expands them back.
(tests/headless.sh run foot -T mini-app -W 40x8 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
read -r ax ay aw ah <<<"$(view_field mini-app "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
super_drag $((ax + aw / 2)) $((ay + ah / 2)) $((screen_w - 8)) $((ay + ah / 2))
sleep 2.5
card_width() { views | python3 -c "import json,sys; print([round(v['frame']['width']) for v in json.load(sys.stdin)['views'] if v['widget'] and v['title'].endswith('mini-app')][0])"; }
wide=$(card_width)
h stipc/feed_key '{"key":"KEY_LEFTMETA","state":true}'; h stipc/feed_key '{"key":"KEY_M","state":true}'
h stipc/feed_key '{"key":"KEY_M","state":false}'; h stipc/feed_key '{"key":"KEY_LEFTMETA","state":false}'
sleep 1.5
check "WG16 Super+M collapses widgets to their icons (a square card)" \
  [ "$(ipc scottland/widgets | python3 -c "import json,sys; print([w['minimized'] for w in json.load(sys.stdin)['widgets'] if w['title']=='mini-app'][0])")/$(card_width)" = "True/96" ]
check "WG16 ...against its screen edge" \
  python3 -c "
import json,subprocess,sys
v=[v for v in json.loads(subprocess.run(['tests/headless.sh','ipc','scottland/layout-state'],capture_output=True,text=True).stdout)['views'] if v['widget'] and v['title'].endswith('mini-app')][0]['frame']
sys.exit(0 if v['x'] + v['width'] >= $screen_w - 40 else 1)"
# Collapsed is a mode: a window widgetized now becomes a collapsed widget, from its first frame.
(tests/headless.sh run foot -T mini2-app -W 40x8 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
read -r ax ay aw ah <<<"$(view_field mini2-app "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
super_drag $((ax + aw / 2)) $((ay + ah / 2)) $((screen_w - 8)) $((ay + ah / 2))
sleep 2.5
card2_width() { views | python3 -c "import json,sys; print([round(v['frame']['width']) for v in json.load(sys.stdin)['views'] if v['widget'] and v['title'].endswith('mini2-app')][0])"; }
check "WG16 a window widgetized while widgets are collapsed is collapsed too" \
  [ "$(ipc scottland/widgets | python3 -c "import json,sys; print([w['minimized'] for w in json.load(sys.stdin)['widgets'] if w['title']=='mini2-app'][0])")/$(card2_width)" = "True/96" ]
# The next tap hides the widgets (WG16's third mode); the one after brings them back expanded.
h stipc/feed_key '{"key":"KEY_LEFTMETA","state":true}'; h stipc/feed_key '{"key":"KEY_M","state":true}'
h stipc/feed_key '{"key":"KEY_M","state":false}'; h stipc/feed_key '{"key":"KEY_LEFTMETA","state":false}'
sleep 1.5
check "WG16 Super+M again hides the widgets off the screen edge" \
  [ "$(ipc scottland/widget-mode | python3 -c "import json,sys; print(json.load(sys.stdin)['mode'])")/$(views | python3 -c "import json,sys; print(all(v['hidden'] for v in json.load(sys.stdin)['views'] if v['widget']))")" = "hidden/True" ]
h stipc/feed_key '{"key":"KEY_LEFTMETA","state":true}'; h stipc/feed_key '{"key":"KEY_M","state":true}'
h stipc/feed_key '{"key":"KEY_M","state":false}'; h stipc/feed_key '{"key":"KEY_LEFTMETA","state":false}'
sleep 1.5
check "WG16 Super+M a third time: the card again" [ "$(card_width)" = "$wide" ]
# Its text was never shown before: it must still be measured (its title is longer than mini-app's).
check "WG16 ...the new one too, with its title" [ "$(card2_width)" -ge "$wide" ]
h window-rules/close-view "{\"id\": $(view_field mini2-app "v['id']")}"
h window-rules/close-view "{\"id\": $(view_field mini-app "v['id']")}"
sleep 2

# WG1: the pointer, not the window's center, enters and leaves the rail.
is_widget_of() { ipc scottland/widgets | python3 -c "import json,sys; print(any(w['title']==sys.argv[1] for w in json.load(sys.stdin)['widgets']))" "$1"; }
(tests/headless.sh run foot -T edge-app -W 100x8 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
read -r ax ay aw ah <<<"$(view_field edge-app "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
super_drag $((ax + aw - 8)) $((ay + ah / 2)) $((screen_w - 6)) $((ay + ah / 2))  # grabbed by its far edge
sleep 2.5
check "WG1 the pointer entering the rail makes a widget (the window's center never reaches it)" [ "$(is_widget_of edge-app)" = True ]
read -r cx cy <<<"$(views | python3 -c "import json,sys; f=[v for v in json.load(sys.stdin)['views'] if v['widget'] and v['title'].endswith('edge-app')][0]['frame']; print(round(f['x'] + f['width'] / 2), round(f['y'] + f['height'] / 2))")"
super_drag $cx $cy $cx $((cy + 80))  # grabbed by its middle, outside the narrow rail zone
sleep 2
check "WG1 a widget moved along its rail stays a widget, wherever it's grabbed" [ "$(is_widget_of edge-app)" = True ]
super_drag $cx $((cy + 80)) $((screen_w / 2)) $((cy + 80))
sleep 2
check "WG1 the pointer leaving the rail makes the window again" [ "$(is_widget_of edge-app)" = False ]
read -r ax ay aw ah <<<"$(view_field edge-app "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
super_drag $((ax + 8)) $((ay + ah / 2)) $((screen_w * 96 / 100 - 12)) $((ay + ah / 2))  # by its near edge: center in the rail
sleep 2.5
check "WG1 the window's center in the rail doesn't make a widget while the pointer isn't" [ "$(is_widget_of edge-app)" = False ]
h window-rules/close-view "{\"id\": $(view_field edge-app "v['id']")}"
sleep 2

# WG18: a finger drag anywhere on a card moves it at once (no long press); a tap still opens it.
(tests/headless.sh run foot -T touchy-app -W 40x8 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
read -r ax ay aw ah <<<"$(view_field touchy-app "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
super_drag $((ax + aw / 2)) $((ay + ah / 2)) $((screen_w - 6)) $((ay + ah / 2))
sleep 2.5
card_center() { views | python3 -c "import json,sys; f=[v for v in json.load(sys.stdin)['views'] if v['widget'] and v['title'].endswith('touchy-app')][0]['frame']; print(round(f['x'] + f['width'] / 2), round(f['y'] + f['height'] / 2))"; }
read -r cx cy <<<"$(card_center)"
h stipc/touch "{\"finger\":0,\"x\":$cx,\"y\":$cy}"
for i in $(seq 1 10); do h stipc/touch "{\"finger\":0,\"x\":$cx,\"y\":$((cy + i * 12))}"; sleep 0.015; done
h stipc/touch_release '{"finger":0}'
sleep 1.5
read -r nx ny <<<"$(card_center)"
check "WG18 a finger drag on a card moves it straight away, still a widget" \
  [ "$(is_widget_of touchy-app)/$(( ny - cy > 90 && ny - cy < 150 ))" = "True/1" ]
h stipc/touch "{\"finger\":0,\"x\":$nx,\"y\":$ny}"; sleep 0.05; h stipc/touch_release '{"finger":0}'
sleep 1.5
check "WG18 a tap on the card still opens its window" [ "$(is_widget_of touchy-app)" = False ]
h window-rules/close-view "{\"id\": $(view_field touchy-app "v['id']")}"
sleep 2

# L29: a window let go of stays above the widgets until a re-grab could no longer continue the
# move (fingers lifted to reset on the touchpad), then the widgets float above it again.
(tests/headless.sh run foot -T under-app -W 40x8 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
read -r ax ay aw ah <<<"$(view_field under-app "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
super_drag $((ax + aw / 2)) $((ay + ah / 2)) $((screen_w - 6)) $((ay + ah / 2))
sleep 2.5
read -r ux uy <<<"$(views | python3 -c "import json,sys; f=[v for v in json.load(sys.stdin)['views'] if v['widget'] and v['title'].endswith('under-app')][0]['frame']; print(round(f['x'] + f['width'] / 2), round(f['y'] + f['height'] / 2))")"
(tests/headless.sh run foot -T cover-app -o colors-dark.background=c00000 -o colors-light.background=c00000 -W 40x8 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)  # red: unlike any card
sleep 1.5
read -r ax ay aw ah <<<"$(view_field cover-app "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
on_top() { ipc window-rules/list-views | python3 -c "import json,sys; print([v['always-on-top'] for v in json.load(sys.stdin) if v['title']=='cover-app'][0])"; }
pixel() { tests/headless.sh run grim -g "$ux,$uy 1x1" -t ppm - 2>/dev/null | tail -c 3 | od -An -tu1 | tr -s ' '; }
card_pixel=$(pixel)
super_drag $((ax + aw / 2)) $((ay + ah / 2)) $((ux - 30)) $uy
sleep 0.6
check "L29 a window just let go of stays above the widgets" [ "$(on_top)/$([ "$(pixel)" != "$card_pixel" ] && echo covered)" = "True/covered" ]
sleep 2.6
check "L29 ...then the widgets float above it again" [ "$(on_top)/$(pixel)" = "False/$card_pixel" ]
h window-rules/close-view "{\"id\": $(view_field cover-app "v['id']")}"
h window-rules/close-view "{\"id\": $(view_field under-app "v['id']")}"
sleep 2

# FS1: full screen is focus. A fullscreen window in front sends the widgets off the screen's edges
# (sliding) and runs the focus hooks ("on"); leaving full screen brings them back ("off").
focus_dir="$headless_dir/focus.d"
focus_record="$focus_dir/../focus-record"
printf '#!/bin/sh\necho "$1" >>"%s"\n' "$focus_record" >"$focus_dir/10-record"; chmod +x "$focus_dir/10-record"
(tests/headless.sh run foot -T docked-app -W 40x8 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
read -r ax ay aw ah <<<"$(view_field docked-app "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
super_drag $((ax + aw / 2)) $((ay + ah / 2)) $((screen_w - 6)) $((ay + ah / 2))
sleep 2.5
docked() { views | python3 -c "import json,sys; v=[v for v in json.load(sys.stdin)['views'] if v['widget'] and v['title'].endswith('docked-app')][0]; print(v['hidden'], round(v['frame']['x']))"; }
read -r _ x0 <<<"$(docked)"
(tests/headless.sh run foot -T full-app -W 40x8 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
h wm-actions/set-fullscreen "{\"view_id\": $(view_field full-app "v['id']"), \"state\": true}"
slid=no
for i in $(seq 1 12); do read -r hid x <<<"$(docked)"; [ "$hid" = False ] && [ "$x" -gt $((x0 + 5)) ] && slid=yes; sleep 0.03; done
sleep 1
check "FS1 full screen: the widgets slide off the screen's edge" [ "$slid/$(docked | cut -d' ' -f1)" = "yes/True" ]
check "FS1 ...and the focus hooks run with on" [ "$(tail -1 "$focus_record" 2>/dev/null)" = on ]
h wm-actions/set-fullscreen "{\"view_id\": $(view_field full-app "v['id']"), \"state\": false}"
sleep 1
read -r hid x <<<"$(docked)"
check "FS1 leaving full screen: the widgets come back to their place" [ "$hid/$(( x - x0 < 3 && x0 - x < 3 ))" = "False/1" ]
check "FS1 ...and the focus hooks run with off" [ "$(tail -1 "$focus_record" 2>/dev/null)" = off ]
h window-rules/close-view "{\"id\": $(view_field full-app "v['id']")}"
h window-rules/close-view "{\"id\": $(view_field docked-app "v['id']")}"
sleep 2

# L29: going to another window during a just-dropped window's hold brings that window forward,
# and the hold ending doesn't put the dropped one back in front of it.
click() { h stipc/move_cursor "{\"x\":$1,\"y\":$2}"; sleep 0.1; h stipc/feed_button '{"combo":"BTN_LEFT","mode":"press"}'; sleep 0.05; h stipc/feed_button '{"combo":"BTN_LEFT","mode":"release"}'; }
(tests/headless.sh run foot -T back-app -o colors-dark.background=c00000 -o colors-light.background=c00000 -W 50x14 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
(tests/headless.sh run foot -T front-app -o colors-dark.background=0000c0 -o colors-light.background=0000c0 -W 50x14 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
read -r fx fy fw fh <<<"$(view_field front-app "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
super_drag $((fx + fw / 2)) $((fy + fh / 2)) $((fx + fw)) $((fy + fh / 2))   # partly off the back one
sleep 3.2
read -r fx fy fw fh <<<"$(view_field front-app "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
read -r kx ky kw kh <<<"$(view_field back-app "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
overlap() { tests/headless.sh run grim -g "$(( (fx + kx + kw) / 2 )),$((ky + kh / 2)) 1x1" -t ppm - 2>/dev/null | tail -c 3 | od -An -tu1 | tr -s ' '; }
super_drag $((fx + fw / 2)) $((fy + fh / 2)) $((fx + fw / 2 + 10)) $((fy + fh / 2))      # dropped: held above
sleep 0.3
click $((kx + 20)) $((ky + kh - 20)); sleep 0.5
check "L29 clicking another window during a drop's hold brings it forward" [ "$(overlap)" = " 192 0 0" ]
sleep 3
check "L29 ...and the hold ending leaves it in front" [ "$(overlap)" = " 192 0 0" ]
h window-rules/close-view "{\"id\": $(view_field front-app "v['id']")}"
h window-rules/close-view "{\"id\": $(view_field back-app "v['id']")}"
sleep 2

# L23: lifting three fingers ends the drag at once (no grace period); a second three-finger
# drag right after is a new drag, which only counts as the same move for Esc (L27).
(tests/headless.sh run foot -T grace-app -W 30x6 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
read -r ax ay aw ah <<<"$(view_field grace-app "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
h stipc/move_cursor "{\"x\":$((ax + aw / 2)),\"y\":$((ay + ah / 2))}"; sleep 0.1
h scottland/test-input '{"swipe":"begin","fingers":3}'
for i in 1 2 3 4; do h scottland/test-input '{"swipe":"update","dx":-15,"dy":0}'; sleep 0.02; done
h scottland/test-input '{"swipe":"end"}'
sleep 0.1
check "L23 three fingers lifted: the drag has ended (no grace period)" \
  [ "$(ipc scottland/test-input '{}' | python3 -c "import json,sys; print(json.load(sys.stdin)['dragging'])")" = False ]
h window-rules/close-view "{\"id\": $(view_field grace-app "v['id']")}"
sleep 1

# WG14: Esc after a re-grab that crossed a form change: a window dropped on the rail (now a
# widget), picked up again at once (a finger reset, within 1 s) and moved: Esc brings the window
# back where it began. (Picked up later it is a new move: tests/esc-return-test.sh.)
(tests/headless.sh run foot -T form-app -W 40x8 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
fx0=$(ipc window-rules/list-views | python3 -c "import json,sys; g=[v['geometry'] for v in json.load(sys.stdin) if v['title']=='form-app'][0]; print(round(g['x']), round(g['y']))")
read -r ax ay aw ah <<<"$(view_field form-app "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
super_drag $((ax + aw / 2)) $((ay + ah / 2)) $((screen_w - 8)) $((ay + ah / 2))
# Wait for the widget it became (state, bounded), then grab it at once.
widget_frame=""
for i in $(seq 1 60); do
  widget_frame=$(views | python3 -c "
import json,sys
w=[v for v in json.load(sys.stdin)['views'] if v['widget'] and not v.get('preview')]
print('%d %d %d %d' % tuple(round(w[0]['frame'][k]) for k in ('x','y','width','height')) if w else '')")
  [[ -n $widget_frame ]] && break
  sleep 0.05
done
read -r wx wy ww wh <<<"$widget_frame"
h stipc/move_cursor "{\"x\":$((wx + ww / 2)),\"y\":$((wy + wh / 2))}"; sleep 0.1
h stipc/feed_key '{"key":"KEY_LEFTMETA","state":true}'; h stipc/feed_button '{"combo":"BTN_LEFT","mode":"press"}'
for i in 1 2 3 4 5 6; do h stipc/move_cursor "{\"x\":$((wx + ww / 2)),\"y\":$((wy + wh / 2 + i * 15))}"; sleep 0.02; done
h stipc/feed_key '{"key":"KEY_ESC","state":true}'; h stipc/feed_key '{"key":"KEY_ESC","state":false}'
h stipc/feed_button '{"combo":"BTN_LEFT","mode":"release"}'; h stipc/feed_key '{"key":"KEY_LEFTMETA","state":false}'
sleep 1.5
check "WG14 Esc after re-grabbing the widget it became: the window is back where the move began" \
  [ "$(ipc window-rules/list-views | python3 -c "import json,sys; g=[v['geometry'] for v in json.load(sys.stdin) if v['title']=='form-app'][0]; print(round(g['x']), round(g['y']))")/$(view_field form-app "not v['hidden']")/$(views | python3 -c "import json,sys; print(sum(1 for v in json.load(sys.stdin)['views'] if v['widget']))")" = "$fx0/True/0" ]
h window-rules/close-view "{\"id\": $(view_field form-app "v['id']")}"
sleep 1

# AT2/AT3: a configured attention source (a list command), by configuration only.
src=$(mktemp -d "${TMPDIR:-$PWD/build}/scottland-test-source.XXXXXX")
mkdir -p "$src/config/scottland/attention.d"
(tests/headless.sh run foot -T src-app -W 30x6 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1
(tests/headless.sh run foot -T src-front -W 30x6 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
src_id=$(view_field src-app "v['id']")
echo "{\"items\": [{\"win\": $src_id, \"tag\": \"t$src_id\"}]}" >"$src/list.json"
cat >"$src/config/scottland/attention.d/test.ini" <<INI
[source]
list = cat $src/list.json
windows = items
window = win
interval = 0.5
answered = touch $src/answered-{tag}
INI
hooks_dir=$(tests/headless.sh run sh -c 'echo $SCOTTLAND_HOOKS')
(tests/headless.sh run env XDG_CONFIG_HOME="$src/config" XDG_STATE_HOME="$src/state" "$hooks_dir/libexec/scottland-attention-sources" >/dev/null 2>&1 &)
sleep 2
check "AT3 a configured source's listed window gets attention" [ "$(view_field src-app "bool(f.get('attention'))")" = True ]
h scottland/attention "{\"window\": $src_id, \"attention\": true, \"source\": \"other\"}"
echo '{"items": []}' >"$src/list.json"
sleep 1.5
check "AT2 the source withdrawing leaves another source's attention" [ "$(view_field src-app "bool(f.get('attention'))")" = True ]
echo "{\"items\": [{\"win\": $src_id, \"tag\": \"t$src_id\"}]}" >"$src/list.json"
sleep 1.5
read -r ax ay aw ah <<<"$(view_field src-app "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
h window-rules/focus-view "{\"id\": $src_id}"
sleep 1
check "AT2 going to the window answers it (attention off, the source's answered command ran)" \
  bash -c "[ \"\$(tests/headless.sh ipc scottland/layout-state | python3 -c \"import json,sys; print([bool(v['frame'].get('attention')) for v in json.load(sys.stdin)['views'] if v['id']==$src_id][0])\")\" = False ] && [ -e '$src/answered-t$src_id' ]"
for t in src-app src-front; do h window-rules/close-view "{\"id\": $(view_field $t "v['id']")}"; done
sleep 1

# L27/WG14: the move chain under real-input sequences (three-finger swipes): a re-grab that ends
# without moving keeps the move's origin; a re-grab begun within the window counts even if its
# first motion comes later; a swipe drag ended by a click leaves no stale state.
(tests/headless.sh run foot -T chain-app -W 30x6 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
geo_of() { ipc window-rules/list-views | python3 -c "
import json,sys
v=[v for v in json.load(sys.stdin) if v['title']==sys.argv[1]][0]; g=v['geometry']; print(round(g['x']), round(g['y']))" "$1"; }
center_of() { view_field "$1" "round(f['x'] + f['width'] / 2), round(f['y'] + f['height'] / 2)"; }
swipe() {  # swipe <title> <updates>: grab it with three fingers, move left-down
  read -r cx cy <<<"$(center_of "$1")"
  h stipc/move_cursor "{\"x\":$cx,\"y\":$cy}"
  h scottland/test-input '{"swipe":"begin","fingers":3}'
  for i in $(seq 1 "$2"); do h scottland/test-input '{"swipe":"update","dx":-20,"dy":10}'; sleep 0.02; done
}
o0=$(geo_of chain-app)
swipe chain-app 4; h scottland/test-input '{"swipe":"end"}'; sleep 0.3
swipe chain-app 0; h scottland/test-input '{"swipe":"end"}'; sleep 0.3     # no motion
swipe chain-app 4
h stipc/feed_key '{"key":"KEY_ESC","state":true}'; h stipc/feed_key '{"key":"KEY_ESC","state":false}'
h scottland/test-input '{"swipe":"end"}'; sleep 1
check "L27 a re-grab that didn't move keeps the move's origin (Esc goes back to the start)" [ "$(geo_of chain-app)" = "$o0" ]
swipe chain-app 4; h scottland/test-input '{"swipe":"end"}'; sleep 1.8
read -r cx cy <<<"$(center_of chain-app)"
h stipc/move_cursor "{\"x\":$cx,\"y\":$cy}"
h scottland/test-input '{"swipe":"begin","fingers":3}'; sleep 1.0          # begun at ~1.9 s, first motion at ~2.9 s
for i in 1 2 3; do h scottland/test-input '{"swipe":"update","dx":-20,"dy":10}'; sleep 0.02; done
h stipc/feed_key '{"key":"KEY_ESC","state":true}'; h stipc/feed_key '{"key":"KEY_ESC","state":false}'
h scottland/test-input '{"swipe":"end"}'; sleep 1
check "L27 a re-grab begun within the window counts, though its first motion came later" [ "$(geo_of chain-app)" = "$o0" ]
sleep 3
swipe chain-app 3
h stipc/feed_button '{"combo":"BTN_LEFT","mode":"full"}'                    # a click ends the swipe drag
for i in 1 2 3; do h scottland/test-input '{"swipe":"update","dx":-20,"dy":10}'; sleep 0.02; done
h scottland/test-input '{"swipe":"end"}'; sleep 3
p1=$(geo_of chain-app)
swipe chain-app 4
h stipc/feed_key '{"key":"KEY_ESC","state":true}'; h stipc/feed_key '{"key":"KEY_ESC","state":false}'
h scottland/test-input '{"swipe":"end"}'; sleep 1
check "L27 after a swipe drag ended by a click, the next move's Esc goes to its own start" [ "$(geo_of chain-app)" = "$p1" ]
h window-rules/close-view "{\"id\": $(view_field chain-app "v['id']")}"
sleep 1

# WG14: Esc never sends a window to another window's origin: a drag cancelled before it moved
# stays put; a glide interrupted by another cancel still lands where it belongs.
(tests/headless.sh run foot -T esc-a -W 30x6 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
(tests/headless.sh run foot -T esc-b -W 30x6 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
h window-rules/configure-view "{\"id\": $(view_field esc-a "v['id']"), \"geometry\": {\"x\": 120, \"y\": 120, \"width\": 260, \"height\": 140}}"
h window-rules/configure-view "{\"id\": $(view_field esc-b "v['id']"), \"geometry\": {\"x\": 700, \"y\": 420, \"width\": 260, \"height\": 140}}"
sleep 1
geo() { ipc window-rules/list-views | python3 -c "
import json,sys
v=[v for v in json.load(sys.stdin) if v['title']==sys.argv[1]][0]; g=v['geometry']; print(round(g['x']), round(g['y']))" "$1"; }
a0=$(geo esc-a); b0=$(geo esc-b)
esc_key() { h stipc/feed_key '{"key":"KEY_ESC","state":true}'; h stipc/feed_key '{"key":"KEY_ESC","state":false}'; }
super_drag 250 190 450 260   # A moves somewhere else (a finished drag)
sleep 0.5
h stipc/move_cursor '{"x":830,"y":490}'; sleep 0.2
h scottland/test-input '{"swipe":"begin","fingers":3}'   # B picked up, not moved yet
esc_key
h scottland/test-input '{"swipe":"end"}'
sleep 0.8
check "WG14 Esc on a drag that hadn't moved: the window stays where it was (not another's origin)" [ "$(geo esc-b)" = "$b0" ]
a1=$(geo esc-a)
read -r ax ay <<<"$a1"
h stipc/move_cursor "{\"x\":$((ax + 130)),\"y\":$((ay + 70))}"; sleep 0.1
h stipc/feed_key '{"key":"KEY_LEFTMETA","state":true}'; h stipc/feed_button '{"combo":"BTN_LEFT","mode":"press"}'
for i in 1 2 3 4 5 6; do h stipc/move_cursor "{\"x\":$((ax + 130 + i * 30)),\"y\":$((ay + 70))}"; sleep 0.02; done
esc_key; h stipc/feed_button '{"combo":"BTN_LEFT","mode":"release"}'; h stipc/feed_key '{"key":"KEY_LEFTMETA","state":false}'
h stipc/move_cursor '{"x":830,"y":490}'; sleep 0.05
h stipc/feed_key '{"key":"KEY_LEFTMETA","state":true}'; h stipc/feed_button '{"combo":"BTN_LEFT","mode":"press"}'
for i in 1 2 3 4 5 6; do h stipc/move_cursor "{\"x\":$((830 - i * 30)),\"y\":490}"; sleep 0.02; done
esc_key; h stipc/feed_button '{"combo":"BTN_LEFT","mode":"release"}'; h stipc/feed_key '{"key":"KEY_LEFTMETA","state":false}'
sleep 1
check "WG14 two cancels in a row: each window back at its own origin" [ "$(geo esc-a)/$(geo esc-b)" = "$a1/$b0" ]
check "WG14 ...and drawn there (no glide offset left behind)" \
  python3 -c "
import json,subprocess,sys
vs=json.loads(subprocess.run(['tests/headless.sh','ipc','window-rules/list-views'],capture_output=True,text=True).stdout)
ls=json.loads(subprocess.run(['tests/headless.sh','ipc','scottland/layout-state'],capture_output=True,text=True).stdout)['views']
ok=True
for t in ('esc-a','esc-b'):
    g=[v for v in vs if v['title']==t][0]['geometry']; f=[v for v in ls if v['title']==t][0]['frame']
    ok &= abs((f['x']+f['width']/2) - (g['x']+g['width']/2)) < 1.5 and abs((f['y']+f['height']/2) - (g['y']+g['height']/2)) < 1.5
sys.exit(0 if ok else 1)"
for t in esc-a esc-b; do h window-rules/close-view "{\"id\": $(view_field $t "v['id']")}"; done
sleep 1

# L20: a resize right after another stays centered.
(tests/headless.sh run foot -T resize-app -W 40x10 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
center0=$(view_field resize-app "(round(f['x'] + f['width'] / 2), round(f['y'] + f['height'] / 2))")
resize() { h stipc/move_cursor '{"x":640,"y":360}'; h stipc/feed_key '{"key":"KEY_LEFTMETA","state":true}'; h stipc/feed_key '{"key":"KEY_LEFTALT","state":true}'
  h stipc/feed_button '{"combo":"BTN_LEFT","mode":"press"}'
  for i in $(seq 1 $1); do h stipc/move_cursor "{\"x\":$((640 + i * 6)),\"y\":$((360 - i * 3))}"; sleep 0.03; done
  h stipc/feed_button '{"combo":"BTN_LEFT","mode":"release"}'; h stipc/feed_key '{"key":"KEY_LEFTALT","state":false}'; h stipc/feed_key '{"key":"KEY_LEFTMETA","state":false}'; }
resize 4; resize 25
sleep 1
check "L20 a resize started right after another stays centered" \
  [ "$(view_field resize-app "(round(f['x'] + f['width'] / 2), round(f['y'] + f['height'] / 2))")" = "$center0" ]
h window-rules/close-view "{\"id\": $(view_field resize-app "v['id']")}"
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
check "WG1 (reload) the window, still on the rail, is a widget again" \
  [ "$(view_field widget-app5 "v['hidden'] and v['widgetized']")" = True ]
h window-rules/close-view "{\"id\": $(view_field widget-app5 "v['id']")}"
sleep 1

# WG5: a reload (scottland-reload marks it) keeps widgets: they're handed to the new plugin.
(tests/headless.sh run foot -T carry-app -W 40x8 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
read -r ax ay aw ah <<<"$(view_field carry-app "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
super_drag $((ax + aw / 2)) $((ay + ah / 2)) $((screen_w - 8)) $((ay + ah / 2))
sleep 2.5
carry_before=$(ipc scottland/widgets | python3 -c "import json,sys; print([w['widget_view'] for w in json.load(sys.stdin)['widgets'] if w['title']=='carry-app'])")
mark=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/scottland/$display.reloading
touch "$mark"
fresh=$headless_dir/libscottland-test-$(date +%s%N).so
cp build/libscottland.so "$fresh"
plugins=$(ipc wayfire/get-config-option '{"option":"core/plugins"}' | python3 -c "import json,sys; print(json.load(sys.stdin)['value'])")
h wayfire/set-config-options "$(python3 -c "import json,sys; print(json.dumps({'core/plugins': ' '.join(sys.argv[2] if p == 'scottland' or '/libscottland-' in p else p for p in sys.argv[1].split())}))" "$plugins" "$fresh")"
sleep 2
rm -f "$mark"
check "WG5 (marked reload) the widget stays, linked, its window still hidden" \
  [ "$(ipc scottland/widgets | python3 -c "import json,sys; print([w['widget_view'] for w in json.load(sys.stdin)['widgets'] if w['title']=='carry-app'])")/$(view_field carry-app "v['hidden'] and v['widgetized']")" = "$carry_before/True" ]
h window-rules/close-view "{\"id\": $(view_field carry-app "v['id']")}"
sleep 2

# WG5: the app's window closing takes the widget with it.
(tests/headless.sh run foot -T widget-app2 -W 40x10 sh -c 'sleep 5; exit 0' >/dev/null 2>&1 &)
sleep 1.5
read -r ax ay aw ah <<<"$(view_field widget-app2 "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
super_drag $((ax + aw / 2)) $((ay + ah / 2)) $((screen_w - 8)) $((ay + ah / 2))
sleep 5
check "WG5 the app's window closing closes its widget" \
  [ "$(views | python3 -c "import json,sys; print(len(json.load(sys.stdin)['views']))")" = 0 ]

# WG5 without systemd scopes (fallback): a widget ignoring SIGTERM still has its first process
# ended (SIGKILL after 2 s).
[ -n "$monitor_pid" ] && kill "$monitor_pid" 2>/dev/null; monitor_pid=
cp "$headless_dir/wayfire.log" "$artifacts/wayfire.log"
tests/headless.sh stop >/dev/null 2>&1
check "stopping the test session ends its color-scheme watcher" bash -c "[ -n '$watcher' ] && ! kill -0 '$watcher' 2>/dev/null"
SCOTTLAND_WIDGET_SCOPE=0 tests/headless.sh start --widgets >/dev/null || { echo "couldn't restart headless Scottland"; exit 1; }
h wayfire/set-config-options '{"scottland/sounds":false}'
(tests/headless.sh run foot --app-id scottland-test-sleeper -T fallback-app -W 40x10 sh -c 'exec sleep 3600' >/dev/null 2>&1 &)
sleep 1.5
read -r ax ay aw ah <<<"$(view_field fallback-app "round(f['x']), round(f['y']), round(f['width']), round(f['height'])")"
super_drag $((ax + aw / 2)) $((ay + ah / 2)) $((screen_w - 8)) $((ay + ah / 2))
sleep 1
first=$(ipc scottland/widgets | python3 -c "import json,sys; w=json.load(sys.stdin)['widgets']; print(w[0]['launcher_pid'] if w else '')")
check "WG5 (no scope) the widget runs outside any scope" \
  bash -c "[ -n '$first' ] && ! grep -q 'scottland-widget-' /proc/$first/cgroup"
sleep 11.5
check "WG5 (no scope) its first process, which ignored SIGTERM, was ended" \
  bash -c "! kill -0 '$first' 2>/dev/null || grep -q '^State:.*Z' /proc/$first/status"
check "WG5 (no scope) the app's window is back" [ "$(view_field fallback-app "not v['hidden']")" = True ]

echo
[ "$fails" -eq 0 ] && echo "all widget checks passed" || echo "$fails check(s) failed"
exit $((fails > 0))
