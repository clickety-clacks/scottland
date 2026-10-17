#!/usr/bin/env python3
"""Switching desktops always closes the current one (W2; keep mode removed, Mike 2026-10-06).

Three layers, each judged by what the next layer down received:

1. scottland-switch with stand-ins for pkexec, qs and Omarchy's window closing: the root helper
   is asked for close mode whatever the remembered choice was, a remembered "keep" (switch.json)
   is deleted, --keep is refused, and Hyprland's windows are closed first when leaving Hyprland.
2. The root helper in a user namespace, with /etc/sddm.conf.d bound to a test directory and
   stand-ins for loginctl, chvt, systemd-run and systemctl (bypasses pkexec/polkit and the real
   login manager): close mode restarts the login manager or switches to an already running
   target and ends the caller; keep mode is refused before anything is written.
3. The switch dialog in a headless session, driven with stipc keys: it draws the warning in the
   theme's urgent color, and Return runs `scottland-switch --to <other>` with no mode; Space
   (the old checkbox toggle) changes nothing; Escape dismisses without switching. The absence
   of the checkbox is checked by looking at the saved screenshot, not asserted.

  tests/omarchy-switch-test.py
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from omarchy_fixture import REPO, Checks, Fixture, Session, screenshot  # noqa: E402

check = Checks()
root = REPO / "build/omarchy-switch-fixture"
switch = REPO / "omarchy/bin/scottland-switch"
helper = REPO / "omarchy/helper/scottland-session-helper"
qs_env = 'printf "qs-env\\tOTHER=%s CURRENT=%s SETTINGS=%s\\n" "$OTHER" "$CURRENT" "${SETTINGS-unset}" >>"$(dirname "$0")/../calls.log"'
fixture = Fixture(root, recorders=["pkexec", "omarchy-osd", "omarchy-hyprland-window-close-all",
                                   "scottland-switch", "chvt", "systemd-run"],
                  commands={"qs": qs_env})
config = root / "config"


def remember_keep():
    (config / "scottland").mkdir(parents=True, exist_ok=True)
    (config / "scottland/switch.json").write_text(json.dumps({"closeCurrent": False}) + "\n")


def run_switch(desktop, *args):
    fixture.log.write_text("")
    env = {"PATH": f"{fixture.bin}:/usr/bin:/bin", "HOME": str(fixture.home),
           "XDG_CONFIG_HOME": str(config), "XDG_CURRENT_DESKTOP": desktop, "XDG_SESSION_ID": "42"}
    result = subprocess.run([str(switch), *args], env=env, text=True, capture_output=True, timeout=30)
    return result, fixture.calls()


# 1. scottland-switch
remember_keep()
result, calls = run_switch("Hyprland", "--to", "scottland")
check("Hyprland -> Scottland with a remembered keep asks the helper for close mode",
      ("pkexec", "/usr/lib/scottland/scottland-session-helper switch scottland close 42") in calls, calls)
check("Hyprland's windows are closed before the helper runs",
      [n for n, _ in calls].index("omarchy-hyprland-window-close-all") < [n for n, _ in calls].index("pkexec")
      if {"pkexec", "omarchy-hyprland-window-close-all"} <= {n for n, _ in calls} else False, calls)
check("the remembered keep choice is deleted", not (config / "scottland/switch.json").exists())

remember_keep()
result, calls = run_switch("Scottland:Wayfire:wlroots", "--to", "hyprland")
check("Scottland -> Hyprland with a remembered keep asks the helper for close mode",
      calls == [("pkexec", "/usr/lib/scottland/scottland-session-helper switch hyprland close 42")], calls)

result, calls = run_switch("Hyprland", "--to", "scottland", "--keep")
check("--keep is refused and nothing is switched", result.returncode == 2 and calls == [],
      (result.returncode, result.stderr, calls))

remember_keep()
result, calls = run_switch("Hyprland")
check("no arguments shows the dialog for the other desktop, with no settings file",
      ("qs", "-p /usr/share/scottland/switch-dialog") in calls
      and ("qs-env", "OTHER=scottland CURRENT=hyprland SETTINGS=unset") in calls, calls)
check("showing the dialog also deletes the remembered keep choice",
      not (config / "scottland/switch.json").exists())

# 2. the root helper, in a user namespace
sessions = root / "sessions"
sddm = root / "sddm.conf.d"
sddm.mkdir()
# loginctl stand-in: sessions from $SESSIONS ("id user seat type state vt desktop" lines).
fixture.command("loginctl", textwrap.dedent(f'''\
    table={sessions}
    case $1 in
      list-sessions) awk '{{print $1, 1000, $2, $3}}' "$table" ;;
      show-session)
        prop=$4
        line=$(awk -v id="$2" '$1 == id' "$table")
        [ -n "$line" ] || exit 1
        set -- $line
        case $prop in
          Type) echo "$4" ;; State) echo "$5" ;; VTNr) echo "$6" ;; Desktop) echo "$7" ;;
          User) echo "$PKEXEC_UID" ;;
        esac ;;
    esac'''))
fixture.command("systemctl")


def run_helper(*args, table=""):
    sessions.write_text(table)
    for conf in sddm.iterdir():
        conf.unlink()
    fixture.log.write_text("")
    script = f'mount --bind {sddm} /etc/sddm.conf.d && exec {helper} "$@"'
    env = {"PATH": f"{fixture.bin}:/usr/bin:/bin", "PKEXEC_UID": str(os.getuid())}
    result = subprocess.run(["unshare", "-rm", "sh", "-c", script, "sh", *args], env=env, text=True,
                            capture_output=True, timeout=30)
    conf = sddm / "zz-scottland.conf"
    return result, fixture.calls(), conf.read_text() if conf.exists() else None


user = subprocess.run(["id", "-nu"], text=True, capture_output=True).stdout.strip()
me = f"5 {user} seat0 wayland active 1 Hyprland\n"
result, calls, conf = run_helper("switch", "hyprland", "keep", "5", table=me)
check("helper refuses keep mode before writing the boot session or switching",
      result.returncode == 2 and conf is None and not [c for c in calls if c[0] != "loginctl"],
      (result.returncode, result.stderr, conf, calls))

result, calls, conf = run_helper("switch", "scottland", "close", "5", table=me)
check("close with no Scottland running: boots Scottland and restarts the login manager",
      result.returncode == 0 and conf and "Session=scottland.desktop" in conf
      and any(n == "systemd-run" and a.endswith(f"scottland-session-helper relogin {user}") for n, a in calls)
      and not any(n == "chvt" for n, _ in calls), (result.returncode, result.stderr, conf, calls))

running = me + f"7 {user} seat0 wayland active 3 Scottland:Wayfire:wlroots\n"
result, calls, conf = run_helper("switch", "scottland", "close", "5", table=running)
check("close with Scottland already running: switches to its VT and ends the caller",
      result.returncode == 0 and ("chvt", "3") in calls and ("loginctl", "terminate-session 5") in calls
      and not any(n == "systemd-run" for n, _ in calls), (result.returncode, result.stderr, calls))

# 3. the dialog, headless
URGENT = (0xa5, 0x55, 0x55)  # Omarchy shell default: no theme in the fixture HOME


def near(color, target, tolerance=24):
    return all(abs(a - b) <= tolerance for a, b in zip(color, target))


def urgent_pixels(image):
    if not image:
        return 0
    rgb = image[2]
    return sum(1 for i in range(0, len(rgb) - 2, 3) if near(rgb[i:i + 3], URGENT))


def dialog_alive(session, pid):
    return bool(session.owns(pid) or [p for p in session.descendants(pid) if session.owns(p)])


with Session(fixture, "hl-switch-dialog", omarchy=False) as session:
    session_config = session.dir / "config/scottland"
    session_config.mkdir(parents=True, exist_ok=True)
    (session_config / "switch.json").write_text(json.dumps({"closeCurrent": False}) + "\n")
    before = urgent_pixels(screenshot(session, "before"))
    dialog = REPO / "omarchy/switch-dialog"
    launch = f"OTHER=hyprland CURRENT=scottland exec qs -p {dialog}"
    fixture.log.write_text("")
    pid = session.spawn(launch, log=session.dir / "dialog.log")
    ok, count = session.wait(lambda: urgent_pixels(screenshot(session, "dialog")) >= 150, timeout=30,
                             interval=0.5)
    check("the dialog draws its warning in the theme's urgent color", ok and before == 0,
          {"before": before, "with dialog": urgent_pixels(screenshot(session, "dialog"))})
    session.tap("KEY_SPACE")
    session.tap("KEY_ENTER")
    ok, calls = fixture.wait_calls(lambda c: ("scottland-switch", "--to hyprland") in c, timeout=10)
    check("Return runs scottland-switch --to hyprland, with no mode", ok and calls == [
        ("scottland-switch", "--to hyprland")], calls)
    gone, _ = session.wait(lambda: not dialog_alive(session, pid), timeout=10)
    check("the dialog closes after switching", gone)
    check("the dialog leaves the remembered choice file alone (it no longer reads or writes it)",
          json.loads((session_config / "switch.json").read_text()) == {"closeCurrent": False})

    fixture.log.write_text("")
    pid = session.spawn(launch, log=session.dir / "dialog2.log")
    ok, _ = session.wait(lambda: urgent_pixels(screenshot(session, "dialog2")) >= 150, timeout=30,
                         interval=0.5)
    session.tap("KEY_ESC")
    gone, _ = session.wait(lambda: not dialog_alive(session, pid), timeout=10)
    check("Escape dismisses the dialog without switching", ok and gone and fixture.calls() == [],
          fixture.calls())
    if dialog_alive(session, pid):
        session.terminate(pid)

    # Evidence to look at: the dialog as drawn (no checkbox, the warning under the title).
    shot = REPO / "build/omarchy-switch-dialog.png"
    session.spawn(launch, log=session.dir / "dialog3.log")
    session.wait(lambda: urgent_pixels(screenshot(session, "dialog3")) >= 150, timeout=30, interval=0.5)
    session.run("grim", str(shot))
    print(f"screenshot: {shot}")
    pid = session.owned(r"quickshell .*switch-dialog")
    session.tap("KEY_ESC")
    if not session.wait(lambda: not any(dialog_alive(session, p) for p in pid), timeout=10)[0]:
        session.terminate(*pid)

sys.exit(check.summary())
