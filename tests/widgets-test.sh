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
mkdir -p "$test_widgets/sleeper" "$test_widgets/sender" "$test_widgets/daemon" "$test_widgets/stubborn"
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
printf '#!/bin/sh\nsetsid -f foot -T "$1" sh -c "exec sleep 600"\nexit 0\n' >"$test_widgets/daemon/start"
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
unit=$(ipc scottland/widgets | python3 -c "import json,sys; w=json.load(sys.stdin)['widgets']; print(w[0]['widget_unit'] if w else '')")
procs=$(cat "/sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/app.slice/$unit/cgroup.procs" 2>/dev/null | tr '\n' ' ')
check "WG5 (timeout) the widget runs in its own scope, with its child" [ "$(echo $procs | wc -w)" -ge 2 ]
check "WG11 a widget with no window yet has no window process (widget_pid 0)" \
  [ "$(ipc scottland/widgets | python3 -c "import json,sys; print(json.load(sys.stdin)['widgets'][0]['widget_pid'])")" = 0 ]
check "WG5 (timeout) the widget launched and the app's window is hidden" \
  [ "$(view_field widget-app4 "v['hidden']")" = True ]
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
ask=$XDG_RUNTIME_DIR/scottland-widgets-test-ask
rm -f "$ask".*
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
rm -f "$ask".*
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

# WG5 without systemd scopes (fallback): a widget ignoring SIGTERM still has its first process
# ended (SIGKILL after 2 s).
[ -n "$monitor_pid" ] && kill "$monitor_pid" 2>/dev/null; monitor_pid=
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
