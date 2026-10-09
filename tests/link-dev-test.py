#!/usr/bin/env python3
"""Exercise link-dev's legacy handover cleanup in an isolated temporary home."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class LinkDevTests(unittest.TestCase):
    def test_removes_known_old_links_and_preserves_other_occupants(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            data = home / "data"
            config = home / "config"
            fake_bin = root / "bin"
            fake_bin.mkdir(parents=True)
            home.mkdir()
            (fake_bin / "systemctl").write_text("#!/bin/sh\nexit 0\n")
            (fake_bin / "systemctl").chmod(0o755)

            env = {
                **os.environ,
                "HOME": str(home),
                "XDG_DATA_HOME": str(data),
                "XDG_CONFIG_HOME": str(config),
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
            }
            dev = data / "scottland/dev"
            autostart = dev / "autostart.d/40-handover"
            handover = dev / "libexec/scottland-handover"
            autostart.parent.mkdir(parents=True)
            handover.parent.mkdir(parents=True)

            autostart.symlink_to(REPO / "omarchy/autostart.d/40-handover")
            handover.symlink_to(data / "scottland/releases/0123456789ab/omarchy/libexec/scottland-handover")
            subprocess.run(["make", "link-dev"], cwd=REPO, env=env, check=True,
                           text=True, capture_output=True)
            self.assertFalse(autostart.is_symlink())
            self.assertFalse(handover.is_symlink())

            autostart.write_text("preserve this user file")
            unknown_target = root / "user-owned-handover"
            unknown_target.write_text("user target")
            handover.symlink_to(unknown_target)
            subprocess.run(["make", "link-dev"], cwd=REPO, env=env, check=True,
                           text=True, capture_output=True)
            self.assertEqual(autostart.read_text(), "preserve this user file")
            self.assertTrue(handover.is_symlink())
            self.assertEqual(handover.resolve(), unknown_target)


if __name__ == "__main__":
    unittest.main()
