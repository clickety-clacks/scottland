#!/usr/bin/env python3
"""Exercise scheduled release pass/fail decisions without GitHub mutations."""
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tests/omarchy-release-check.sh"


def exercise(audit_passes, release, branch_exists=False, pr_succeeds=True):
    with tempfile.TemporaryDirectory(prefix="scottland-release-check-") as tmp:
        work = Path(tmp)
        (work / "omarchy").mkdir()
        (work / "omarchy/tested-release").write_text("v4.5.0\n")
        (work / "omarchy/supported-series").write_text("4.5\n")
        (work / "packaging/arch").mkdir(parents=True)
        (work / "packaging/arch/PKGBUILD").write_text("pkgver=0.4.5\npkgrel=1\n")
        bin_dir = work / "bin"
        bin_dir.mkdir()
        log = work / "calls"

        def command(name, body):
            path = bin_dir / name
            path.write_text("#!/bin/bash\nset -eu\n" + body + "\n")
            path.chmod(0o755)

        command("gh", r'''
printf 'gh %s\n' "$*" >>"$CALLS"
if [[ $1 == api && $2 == *pulls* ]]; then exit 0; fi
if [[ $1 == api ]]; then printf 'v4.5.0\n%s\n' "$NEW_RELEASE"; exit 0; fi
if [[ $1 == issue && $2 == list ]]; then exit 0; fi
if [[ $1 == pr && $2 == create ]]; then exit "$PR_STATUS"; fi
''')
        command("git", r'''
printf 'git %s\n' "$*" >>"$CALLS"
if [[ $1 == clone ]]; then mkdir -p "${@: -1}"; fi
if [[ $1 == -C ]]; then printf '0123456789abcdef0123456789abcdef01234567\n'; fi
if [[ $1 == ls-remote ]]; then exit "$BRANCH_STATUS"; fi
''')
        command("docker", '''
printf 'docker %s\n' "$*" >>"$CALLS"
exit "${AUDIT_STATUS:?}"
''')
        env = dict(os.environ, PATH=f"{bin_dir}:/usr/bin:/bin", CALLS=str(log),
                   AUDIT_STATUS="0" if audit_passes else "1",
                   BRANCH_STATUS="0" if branch_exists else "2",
                   PR_STATUS="0" if pr_succeeds else "1",
                   NEW_RELEASE=release,
                   GITHUB_REPOSITORY="example/scottland", GITHUB_SERVER_URL="https://github.com",
                   GITHUB_RUN_ID="12345")
        result = subprocess.run(["bash", str(SCRIPT)], cwd=work, env=env,
                                text=True, capture_output=True, timeout=15)
        calls = log.read_text()
        if audit_passes:
            assert result.returncode == (0 if pr_succeeds else 1), result.stderr
            assert "gh pr create" in calls and "gh issue create" not in calls, calls
            if branch_exists:
                assert "git push" not in calls, calls
                assert (work / "omarchy/tested-release").read_text() == "v4.5.0\n"
            else:
                assert (work / "omarchy/tested-release").read_text() == release + "\n"
                expected = "4.5\n"
                if release.startswith("v4.6."):
                    expected += "4.6\n"
                assert (work / "omarchy/supported-series").read_text() == expected
                assert "pkgrel=2\n" in (work / "packaging/arch/PKGBUILD").read_text()
        else:
            assert result.returncode == 1, result.stderr
            assert (work / "omarchy/tested-release").read_text() == "v4.5.0\n"
            assert "pkgrel=1\n" in (work / "packaging/arch/PKGBUILD").read_text()
            assert "gh issue create" in calls and "gh pr create" not in calls, calls


exercise(True, "v4.5.1")
exercise(True, "v4.6.0")
exercise(True, "v4.6.0", branch_exists=True, pr_succeeds=False)
exercise(False, "v4.6.0")
print("new release pass proposes tested series; failure files fix issue without widening")
