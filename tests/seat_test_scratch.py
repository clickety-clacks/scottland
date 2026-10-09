"""Unique, owned output scratch for the manual real-seat tests."""

import os
import tempfile
import uuid
from pathlib import Path


class OwnedScratch:
    """Create one private run directory and track only files this run may replace/remove."""

    def __init__(self, parent, prefix="seat-"):
        self.parent = Path(parent).resolve()
        self.parent.mkdir(parents=True, exist_ok=True)
        self.path = Path(tempfile.mkdtemp(prefix=prefix, dir=self.parent))
        self._owner = self.path / ".scottland-seat-test-owner"
        self._owner_record = f"{os.getpid()} {uuid.uuid4().hex}\n"
        fd = os.open(self._owner, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as owner:
            owner.write(self._owner_record)
        self._outputs = set()

    def _verify_owner(self):
        if self.path.is_symlink() or not self.path.is_dir() or self.path.parent != self.parent:
            raise RuntimeError("seat-test scratch is no longer the owned directory")
        if self._owner.is_symlink() or not self._owner.is_file():
            raise RuntimeError("seat-test scratch owner record is missing or redirected")
        if self._owner.read_text(encoding="utf-8") != self._owner_record:
            raise RuntimeError("seat-test scratch owner record changed")

    def output(self, name):
        """Return a basename in this run directory, refusing any unowned occupant."""
        if not isinstance(name, str) or name in ("", ".", "..") or Path(name).name != name:
            raise ValueError("seat-test output names must be plain basenames")
        self._verify_owner()
        path = self.path / name
        if path in self._outputs:
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise RuntimeError(f"owned seat-test output was redirected: {name}")
            return path
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"refusing to overwrite unowned seat-test output: {name}")
        self._outputs.add(path)
        return path

    def remove(self, path):
        """Remove one exact output path previously allocated by this run."""
        path = Path(path)
        if path not in self._outputs:
            raise ValueError("refusing to remove an unowned seat-test output")
        self._verify_owner()
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise RuntimeError("refusing to remove a redirected seat-test output")
        path.unlink(missing_ok=True)

    def remove_suffix(self, suffix):
        """Remove only this run's allocated files with the requested suffix."""
        for path in tuple(self._outputs):
            if path.suffix == suffix:
                self.remove(path)
