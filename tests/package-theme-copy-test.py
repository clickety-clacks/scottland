#!/usr/bin/env python3
"""Exercise the adapter package function without compiling or installing packages.

Requires GNU cp/tar, fakeroot and a C compiler. All writes and fake ownership
changes stay in temporary directories. The fault library applies only to cp.
"""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class ThemePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="package-theme-copy-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        shutil.copytree(REPO / "omarchy", self.source / "omarchy", symlinks=True)
        shutil.copy2(REPO / "LICENSE", self.source / "LICENSE")
        recipe = self.source / "packaging/arch"
        recipe.mkdir(parents=True)
        shutil.copy2(REPO / "packaging/arch/PKGBUILD", recipe / "PKGBUILD")
        self.env = {"PATH": "/usr/bin:/bin", "HOME": str(self.root), "LC_ALL": "C",
                    "CASE_ROOT": str(self.root)}

    def package(self, foreign_owner=False, fault=False, mask="022"):
        self.env.update(FOREIGN_OWNER=str(int(foreign_owner)), COPY_FAULT=str(int(fault)),
                        PACKAGE_UMASK=mask)
        if fault:
            # Reject ownership syscalls only in the cp subprocess. This models
            # the observed EINVAL, not the unproven cause on a particular host.
            fault_source = self.root / "ownership-fault.c"
            fault_source.write_text("""#include <errno.h>
#include <sys/types.h>
int chown(const char *p, uid_t u, gid_t g) { errno=EINVAL; return -1; }
int lchown(const char *p, uid_t u, gid_t g) { errno=EINVAL; return -1; }
int fchown(int f, uid_t u, gid_t g) { errno=EINVAL; return -1; }
int fchownat(int f, const char *p, uid_t u, gid_t g, int flags) { errno=EINVAL; return -1; }
""")
            subprocess.run(["cc", "-shared", "-fPIC", str(fault_source), "-o",
                            str(self.root / "ownership-fault.so")], check=True,
                           env=self.env, capture_output=True, timeout=30)
            commands = self.root / "commands"
            commands.mkdir()
            cp = commands / "cp"
            cp.write_text('#!/bin/sh\nexec env LD_PRELOAD="$CASE_ROOT/ownership-fault.so:$LD_PRELOAD" /usr/bin/cp "$@"\n')
            cp.chmod(0o755)
        script = r'''
set -euo pipefail
umask "$PACKAGE_UMASK"
startdir="$CASE_ROOT/source/packaging/arch"
source "$startdir/PKGBUILD"
pkgname=scottland-omarchy
pkgdir="$CASE_ROOT/pkg"
srcdir="$CASE_ROOT/src"
mkdir -p "$pkgdir" "$srcdir"
if [[ $FOREIGN_OWNER == 1 ]]; then
  chown -hR 12345:23456 "$_root/omarchy/themes"
fi
if [[ $COPY_FAULT == 1 ]]; then
  export PATH="$CASE_ROOT/commands:$PATH"
fi
package_scottland-omarchy
tar --format=pax -cf "$CASE_ROOT/payload.tar" -C "$pkgdir" .
'''
        result = subprocess.run(["fakeroot", "--", "bash", "-c", script], env=self.env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with tarfile.open(self.root / "payload.tar") as payload:
            members = {m.name.removeprefix("./"): m for m in payload.getmembers()}
            # Check all packaged entries' numeric owners, not just theme files.
            for name, member in members.items():
                self.assertEqual((member.uid, member.gid), (0, 0), name)
            themes = self.source / "omarchy/themes"
            theme_prefix = "usr/share/scottland-omarchy/themes/"
            expected = {str(p.relative_to(themes)) for p in themes.rglob("*")}
            actual = {p.removeprefix(theme_prefix) for p in members
                      if p.startswith(theme_prefix)}
            self.assertEqual(actual, expected)
            for path in themes.rglob("*"):
                name = theme_prefix + str(path.relative_to(themes))
                member = members[name]
                self.assertEqual(member.mode, path.lstat().st_mode & 0o7777, name)
                if path.is_symlink():
                    self.assertTrue(member.issym(), name)
                    self.assertEqual(member.linkname, os.readlink(path), name)
                elif path.is_dir():
                    self.assertTrue(member.isdir(), name)
                else:
                    self.assertTrue(member.isfile(), name)
                    self.assertEqual(hashlib.sha256(payload.extractfile(member).read()).digest(),
                                     hashlib.sha256(path.read_bytes()).digest(), name)

    def test_package_payload_modes_links_and_root_ownership(self):
        self.package()

    def test_checkout_ownership_does_not_enter_package(self):
        self.package(foreign_owner=True)

    def test_ownership_einval_does_not_block_theme_copy(self):
        self.package(foreign_owner=True, fault=True)

    def test_restrictive_umask_keeps_theme_modes(self):
        self.package(mask="077")


if __name__ == "__main__":
    unittest.main(verbosity=2)
