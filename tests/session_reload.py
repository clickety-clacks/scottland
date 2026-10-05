"""Reload the headless session a test runs in, the way a user does: the real scottland-reload
(receipt, handover, acknowledgment; docs/main-loop.md "Reload"). Plugin copies stay in a private
directory under build/, never in the machine's runtime directory."""
import os, subprocess, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def reload_session(source=None, timeout=30):
    copies = Path(tempfile.mkdtemp(prefix='reload-', dir=REPO / 'build'))
    env = dict(os.environ, SCOTTLAND_TEST_RELOAD_DIR=str(copies), SCOTTLAND_EXEC='1',
               SCOTTLAND_RELOAD_SOURCE=str(source or REPO / 'build/libscottland.so'),
               SCOTTLAND_RELOAD_TIMEOUT=str(timeout))
    command = [str(REPO / 'core/session/scottland-reload')]
    if not os.environ.get('WAYFIRE_SOCKET'):
        # A test driving the session from outside: run the script inside it.
        command = [str(REPO / 'tests/headless.sh'), 'run', 'env', *(f'{k}={env[k]}' for k in
                   ('SCOTTLAND_TEST_RELOAD_DIR', 'SCOTTLAND_RELOAD_SOURCE', 'SCOTTLAND_RELOAD_TIMEOUT')), *command]
        env.pop('SCOTTLAND_EXEC')
    done = subprocess.run(command, env=env, capture_output=True, text=True, timeout=timeout + 60)
    if done.returncode != 0:
        raise RuntimeError(f'scottland-reload failed ({done.returncode}): {done.stdout}{done.stderr}')
    return done.stdout
