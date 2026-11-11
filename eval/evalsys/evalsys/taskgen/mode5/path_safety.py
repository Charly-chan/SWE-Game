"""Fail-closed filesystem validation for untrusted Mode 5 trees."""

from __future__ import annotations

import os
from pathlib import Path
import stat


def reject_links(root: Path, *, label: str) -> None:
    """Reject symlinks and Windows reparse points without following them."""
    pending = [Path(root)]
    while pending:
        path = pending.pop()
        try:
            info = path.lstat()
        except OSError as exc:
            raise ValueError(f"{label} contains an unreadable path: {path}") from exc
        is_reparse = bool(getattr(info, "st_file_attributes", 0) & 0x400)
        is_junction = bool(getattr(path, "is_junction", lambda: False)())
        if stat.S_ISLNK(info.st_mode) or is_reparse or is_junction:
            raise ValueError(f"{label} contains a symlink or reparse point: {path}")
        if stat.S_ISDIR(info.st_mode):
            try:
                with os.scandir(path) as entries:
                    pending.extend(Path(entry.path) for entry in entries)
            except OSError as exc:
                raise ValueError(
                    f"{label} contains an unreadable directory: {path}"
                ) from exc
