#!/usr/bin/env python3
"""Exercise skill discovery/migration and package resource staging without a compositor.

Package staging supplies placeholders for unrelated compiled artifacts and bypasses Meson
installation. It proves only the skill resources and login hook, not a package build/install.
"""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / "core/agents/skills/scottland"


class SkillInstallation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="scottland-skill-install-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_command(self, argv, **kwargs):
        result = subprocess.run(argv, cwd=REPO, text=True, capture_output=True,
                                timeout=30, **kwargs)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_hooks_migrate_legacy_link_without_changing_its_source(self):
        hooks = self.root / "hooks"
        hooks.mkdir()
        legacy = self.root / "legacy-agents"
        (legacy / "skills").mkdir(parents=True)
        (legacy / "skills/personal").write_text("keep me\n")
        (hooks / "agents").symlink_to(legacy)
        self.run_command(["make", "--no-print-directory", "hooks", f"HOOKS_DIR={hooks}"])
        self.assertFalse((hooks / "agents").is_symlink())
        self.assertEqual((legacy / "skills/personal").read_text(), "keep me\n")
        for source in REPO.glob("*/agents/skills/*/SKILL.md"):
            self.assertEqual((hooks / "agents/skills" / source.parent.name / "SKILL.md")
                             .read_bytes(), source.read_bytes())
        stale = hooks / "agents/skills/scottland-removed"
        stale.symlink_to(self.root / "removed")
        self.run_command(["make", "--no-print-directory", "hooks", f"HOOKS_DIR={hooks}"])
        self.assertFalse(os.path.lexists(stale))
        self.assertEqual((legacy / "skills/personal").read_text(), "keep me\n")

    def test_package_stages_complete_skill_and_installed_hook_discovers_it(self):
        src = self.root / "src"
        for name in ("cargo/release/attentiond", "tools/bin/scottland", "base.bundle", "base.commit"):
            path = src / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("unrelated build placeholder\n")
        pkg = self.root / "pkg"
        self.run_command([
            "bash", "-eu", "-c",
            'startdir="$1/packaging/arch"; srcdir="$2"; pkgdir="$3"; '
            'source "$startdir/PKGBUILD"; meson() { :; }; package_scottland',
            "skill-staging", str(REPO), str(src), str(pkg)])
        installed = pkg / "usr/share/scottland/agents/skills/scottland"
        expected = {p.relative_to(SKILL): p.read_bytes() for p in SKILL.rglob("*") if p.is_file()}
        actual = {p.relative_to(installed): p.read_bytes()
                  for p in installed.rglob("*") if p.is_file()}
        self.assertEqual(actual, expected)
        self.assertIn(Path("examples/title-widget/shell.qml"), actual)
        self.assertIn(Path("examples/title-widget/widget.toml"), actual)
        home = self.root / "home"
        (home / ".codex").mkdir(parents=True)
        env = dict(os.environ, HOME=str(home),
                   SCOTTLAND_HOOKS=str(pkg / "usr/share/scottland"))
        self.run_command([str(pkg / "usr/lib/scottland/autostart.d/02-link-agent-skills")], env=env)
        link = home / ".codex/skills/scottland"
        self.assertEqual((link / "SKILL.md").read_bytes(), (SKILL / "SKILL.md").read_bytes())
        self.assertEqual((link / "examples/title-widget/shell.qml").read_bytes(),
                         (SKILL / "examples/title-widget/shell.qml").read_bytes())


if __name__ == "__main__":
    unittest.main()
