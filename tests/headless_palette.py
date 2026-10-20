"""Helpers for selecting palette files owned by a headless session."""

import os
from pathlib import Path


def require_private_palette(path):
    """Refuse to use the login session's palette as a headless test fixture."""
    candidate = Path(path)
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    live = runtime / "scottland" / candidate.name
    if candidate.resolve(strict=False) == live.resolve(strict=False):
        raise RuntimeError(
            "headless palette resolves to the shared runtime palette; "
            "start an isolated headless session before seeding it"
        )
    return candidate
