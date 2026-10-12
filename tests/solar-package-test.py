#!/usr/bin/env python3
"""Exercise the split PKGBUILD functions and verify file/optdepend ownership."""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]

with tempfile.TemporaryDirectory(prefix="solar-package-") as temp:
    scratch = Path(temp)
    startdir = ROOT/"packaging/arch"
    fakebin = scratch/"bin"
    fakebin.mkdir()
    meson = fakebin/"meson"
    meson.write_text("#!/bin/sh\n# Meson install payload is unrelated to solar file ownership.\nexit 0\n")
    meson.chmod(0o755)
    pkgroot = scratch/"packages"
    srcdir = scratch/"src"
    (srcdir/"tools/bin").mkdir(parents=True)

    command = r'''
set -e
shopt -s nullglob
startdir="$STARTDIR"
source "$STARTDIR/PKGBUILD"
pkgver=0.3.0
pkgrel=1
srcdir="$SRCDIR"
pkgdir="$PKGROOT/core"
pkgname=scottland
package_scottland
printf 'CORE_OPTS=%s\n' "${optdepends[*]}"
pkgdir="$PKGROOT/sunlight"
pkgname=scottland-sunlight
package_scottland-sunlight
printf 'SUNLIGHT_DEPENDS=%s\n' "${depends[*]}"
printf 'SUNLIGHT_OPTS=%s\n' "${optdepends[*]}"
'''
    result = subprocess.run(["bash", "-c", command], env=dict(os.environ, PATH=str(fakebin)+":"+os.environ.get("PATH", ""),
                                                                     STARTDIR=str(startdir), SRCDIR=str(srcdir), PKGROOT=str(pkgroot)),
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout+result.stderr
    core = pkgroot/"core/usr/lib/scottland"
    sunlight = pkgroot/"sunlight/usr/lib/scottland"
    assert not (core/"autostart.d/07-solar-theme").exists()
    assert not (core/"libexec/scottland-solar-theme").exists()
    assert (sunlight/"autostart.d/07-solar-theme").is_file()
    assert (sunlight/"libexec/scottland-solar-theme").is_file()
    assert "geoclue" not in next(line for line in result.stdout.splitlines() if line.startswith("CORE_OPTS="))
    assert "geoclue" in next(line for line in result.stdout.splitlines() if line.startswith("SUNLIGHT_OPTS="))
    assert "scottland=0.3.0-1" in next(line for line in result.stdout.splitlines() if line.startswith("SUNLIGHT_DEPENDS="))
    print("PASS core package excludes Sunlight hook/script and Geoclue optdepend")
    print("PASS standalone scottland-sunlight owns the hook/script and Geoclue optdepend")
    print("PASS standalone package requires the matching core version")
