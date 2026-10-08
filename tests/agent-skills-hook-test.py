#!/usr/bin/env python3
"""Installed skills follow the selected install without replacing personal skills."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest


HOOK = Path(__file__).resolve().parents[1] / "core/autostart.d/02-link-agent-skills"


class AgentSkillLinks(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.home = root / "home"
        self.skills = self.home / ".codex/skills"
        self.skills.mkdir(parents=True)
        self.stock = root / "hooks/agents/skills/scottland"
        self.stock.mkdir(parents=True)
        (self.stock / "SKILL.md").write_text("stock skill\n")
        self.env = dict(os.environ, HOME=str(self.home), SCOTTLAND_HOOKS=str(root / "hooks"))
        self.link = self.skills / "scottland"

    def run_concurrent_hooks(self) -> list[subprocess.CompletedProcess[str]]:
        processes = [subprocess.Popen([str(HOOK)], env=self.env, text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for _ in range(3)]
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            results.append(subprocess.CompletedProcess(process.args, process.returncode,
                                                   stdout, stderr))
        return results

    def test_custom_link_survives_concurrent_hooks_and_is_reported(self) -> None:
        custom = Path(self.temp.name) / "custom-skill"
        custom.mkdir()
        (custom / "SKILL.md").write_text("personal skill\n")
        self.link.symlink_to(custom)
        results = self.run_concurrent_hooks()
        self.assertTrue(all(result.returncode == 0 for result in results), results)
        self.assertEqual(os.readlink(self.link), str(custom))
        self.assertTrue(all("keeping existing agent skill" in result.stderr
                            for result in results), results)

    def test_custom_directory_and_dangling_link_are_kept(self) -> None:
        self.link.mkdir()
        (self.link / "SKILL.md").write_text("personal skill\n")
        result = self.run_concurrent_hooks()
        self.assertTrue(all(item.returncode == 0 for item in result), result)
        self.assertEqual((self.link / "SKILL.md").read_text(), "personal skill\n")
        self.link.rename(self.skills / "saved-skill")
        self.link.symlink_to(self.skills / "missing-skill")
        result = self.run_concurrent_hooks()
        self.assertTrue(all(item.returncode == 0 for item in result), result)
        self.assertEqual(os.readlink(self.link), str(self.skills / "missing-skill"))

    def test_concurrent_hooks_create_one_stock_link(self) -> None:
        results = self.run_concurrent_hooks()
        self.assertTrue(all(result.returncode == 0 for result in results), results)
        self.assertEqual(os.readlink(self.link), str(self.stock))
        self.assertTrue(all("keeping existing agent skill" not in result.stderr
                            for result in results), results)

    def test_core_and_integration_are_discoverable_in_installed_harnesses(self) -> None:
        harnesses = [".claude", ".codex", ".agents", ".pi/agent", ".hermes",
                     ".hermes/profiles/example"]
        for harness in harnesses:
            (self.home / harness).mkdir(parents=True, exist_ok=True)
        adapter = self.stock.parent / "scottland-example"
        adapter.mkdir()
        (adapter / "SKILL.md").write_text("integration skill\n")
        result = subprocess.run([str(HOOK)], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for harness in harnesses:
            skills = self.home / harness / "skills"
            self.assertEqual((skills / "scottland/SKILL.md").read_text(), "stock skill\n")
            self.assertEqual((skills / "scottland-example/SKILL.md").read_text(),
                             "integration skill\n")
        (adapter / "SKILL.md").unlink()
        adapter.rmdir()
        result = subprocess.run([str(HOOK)], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for harness in harnesses:
            skills = self.home / harness / "skills"
            self.assertFalse(os.path.lexists(skills / "scottland-example"))
            self.assertEqual((skills / "scottland/SKILL.md").read_text(), "stock skill\n")

    def test_owned_packaged_link_switches_to_selected_install(self) -> None:
        self.link.symlink_to("/usr/share/scottland/agents/skills/scottland")
        results = self.run_concurrent_hooks()
        self.assertTrue(all(result.returncode == 0 for result in results), results)
        self.assertEqual(os.readlink(self.link), str(self.stock))
        self.assertEqual((self.link / "SKILL.md").read_text(), "stock skill\n")

    def test_absent_harness_is_not_created_and_personal_file_is_kept(self) -> None:
        self.link.write_text("personal skill\n")
        results = self.run_concurrent_hooks()
        self.assertTrue(all(result.returncode == 0 for result in results), results)
        self.assertEqual(self.link.read_text(), "personal skill\n")
        self.assertFalse((self.home / ".claude").exists())


if __name__ == "__main__":
    unittest.main()
