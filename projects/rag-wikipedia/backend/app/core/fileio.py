"""Crash-safe file writes.

The runtime config and audit_report.json are written by one process (the CLI
audit) and read by another (the API serving /quality). A plain write leaves a
window in which the reader observes a truncated file, which is how a JSON
decode error reaches an HTTP handler.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Write *text* to *path* so no reader ever observes a partial file.

    Writes a sibling temp file, fsyncs it, then renames over the target.
    os.replace is atomic within a filesystem, so the target is either the old
    contents or the new ones - never half of either.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding=encoding,
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        temp_path = Path(handle.name)

    try:
        os.replace(temp_path, path)
    except OSError:
        temp_path.unlink(missing_ok=True)
        raise
