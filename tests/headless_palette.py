"""Write palette fixtures through their headless session namespace."""

import subprocess


def write_session_file(path, contents):
    """Write contents to a path visible inside the running headless session."""
    subprocess.run(
        ["tests/headless.sh", "run", "sh", "-c", 'cat >"$1"', "sh", str(path)],
        input=contents,
        text=True,
        check=True,
    )


def replace_session_file(source, destination):
    """Atomically replace a session file from inside the headless namespace."""
    subprocess.run(
        [
            "tests/headless.sh",
            "run",
            "python3",
            "-c",
            "import os,sys; os.replace(sys.argv[1], sys.argv[2])",
            str(source),
            str(destination),
        ],
        check=True,
    )
