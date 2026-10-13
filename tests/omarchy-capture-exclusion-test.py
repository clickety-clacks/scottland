#!/usr/bin/env python3
"""Password-manager capture exclusions: the user is told the truth (adapter-gaps AG01).

Omarchy hides 1Password and Bitwarden from screen sharing with no_screen_share window rules.
Scottland cannot enforce that (see docs/adapter-gaps.md, AG01): its capture paths copy the
composed screen. So the O20 report must list every such rule, and the claim it makes must hold.

Isolated headless --omarchy session loading the installed, unchanged apps/1password.lua and
apps/bitwarden.lua plus a user's own rule. Oracles: the generated report, and a screencopy of a
window whose app-id the 1Password rule matches (tests/solid-color-app.py --app-id 1Password, a
window painted one known color).

  tests/omarchy-capture-exclusion-test.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from omarchy_fixture import REPO, Checks, Fixture, Session, pixel, screenshot  # noqa: E402

COLOR = (0x12, 0xAB, 0x34)
check = Checks()
fixture = Fixture(REPO / "build/omarchy-capture-fixture",
                  modules=["default.hypr.apps.1password", "default.hypr.apps.bitwarden"],
                  lua='o.window("^(org.keepassxc.KeePassXC)$", { no_screen_share = true })\n')


def views(session):
    reply = session.ipc("window-rules/list-views")
    return reply if isinstance(reply, list) else []


with Session(fixture, "hl-omarchy-capture") as session:
    report = (session.dir / "state/scottland/omarchy-overrides.txt").read_text()
    group = report.split("## Not hidden from screen sharing in Scottland", 1)[-1].split("\n## ", 1)[0]
    own = report.split("## Your own shortcuts that don't work in Scottland", 1)[-1].split("\n## ", 1)[0]
    check("report has a capture-exclusion group explaining Scottland cannot hide windows",
          "Scottland cannot" in group, report[:600])
    check("1Password's rule is listed as an Omarchy default",
          "Window rule: class ^(1[p|P]assword)$" in group and "[Omarchy default]" in
          [l for l in group.splitlines() if "1[p|P]assword" in l][0], group)
    check("Bitwarden's rules (app and browser extension) are listed",
          "class ^(Bitwarden)$" in group and "chrome-nngceckbapebfimnlniiiahkandclblb" in group, group)
    check("the user's own rule is called out first, as their own",
          "KeePassXC" in own and "[Your custom/changed shortcut]" in own, own)

    app = session.spawn(f"exec python3 {REPO}/tests/solid-color-app.py capture '#12AB34' --app-id 1Password")
    ok, found = session.wait(lambda: [v for v in views(session)
                                      if v.get("app-id") == "1Password" and v.get("mapped")], timeout=20)
    if check("a window the 1Password rule matches maps", ok):
        box = found[0]["geometry"]
        point = (int(box["x"] + box["width"] * 3 / 4), int(box["y"] + box["height"] * 3 / 4))
        seen = {}
        ok, _ = session.wait(lambda: seen.update(shot=screenshot(session, "capture")) or (
            seen["shot"] is not None and pixel(seen["shot"], *point) == COLOR), timeout=10, interval=0.3)
        check("as the report says, that window appears in a screen capture", ok,
              pixel(seen["shot"], *point) if seen.get("shot") else "no capture")
    session.terminate(app)

sys.exit(check.summary())
