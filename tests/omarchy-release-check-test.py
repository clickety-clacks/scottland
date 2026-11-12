#!/usr/bin/env python3
"""Exercise scheduled release pass/fail decisions without GitHub mutations."""
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tests/omarchy-release-check.sh"


def exercise(audit_passes, release):
    with tempfile.TemporaryDirectory(prefix="scottland-release-check-") as tmp:
        work = Path(tmp)
        (work / "omarchy").mkdir()
        (work / "omarchy/tested-release").write_text("v4.5.0\n")
        (work / "omarchy/supported-series").write_text("4.5\n")
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
''')
        command("git", r'''
printf 'git %s\n' "$*" >>"$CALLS"
if [[ $1 == clone ]]; then mkdir -p "${@: -1}"; fi
if [[ $1 == -C ]]; then printf '0123456789abcdef0123456789abcdef01234567\n'; fi
''')
        command("docker", '''
printf 'docker %s\n' "$*" >>"$CALLS"
exit "${AUDIT_STATUS:?}"
''')
        env = dict(os.environ, PATH=f"{bin_dir}:/usr/bin:/bin", CALLS=str(log),
                   AUDIT_STATUS="0" if audit_passes else "1",
                   NEW_RELEASE=release,
                   GITHUB_REPOSITORY="example/scottland", GITHUB_SERVER_URL="https://github.com",
                   GITHUB_RUN_ID="12345")
        result = subprocess.run(["bash", str(SCRIPT)], cwd=work, env=env,
                                text=True, capture_output=True, timeout=15)
        calls = log.read_text()
        if audit_passes:
            assert result.returncode == 0, result.stderr
            assert (work / "omarchy/tested-release").read_text() == release + "\n"
            assert (work / "omarchy/supported-series").read_text() == ".".join(release[1:].split(".")[:2]) + "\n"
            assert "gh pr create" in calls and "gh issue create" not in calls, calls
        else:
            assert result.returncode == 1, result.stderr
            assert (work / "omarchy/tested-release").read_text() == "v4.5.0\n"
            assert "gh issue create" in calls and "gh pr create" not in calls, calls


exercise(True, "v4.5.1")
exercise(True, "v4.6.0")
exercise(False, "v4.6.0")
print("new release pass proposes tested series; failure files fix issue without widening")
