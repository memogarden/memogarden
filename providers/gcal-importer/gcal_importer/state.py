"""Per-subject sync state for the gcal provider (RFC-012 §4.6 file pattern).

Derived state kept out of the database: deleting the file just forces a
reseed (lost sync tokens → 410 → reseed) and an etag-map rebuild —
correctness never depends on it, only efficiency (gmail
incremental-sync-design discipline). Whole-doc overwrite, atomic tmp +
rename (the gmail cursor discipline).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def default_state_path(subject: str) -> str:
    """Subject-qualified path — per-account cursors must never be shared
    (RFC-012 M5: a provider-level path would merge every account's cursor)."""
    safe = "".join(c if c.isalnum() or c in "@._-" else "_" for c in subject)
    base = Path("~/.local/share/memogarden").expanduser()
    return str(base / f"gcal-sync-state--{safe}.json")


def load_state(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        doc = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}  # a corrupt state file is a reseed, not a crash
    return doc if isinstance(doc, dict) else {}


def store_state(path: str, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".gcal-state-")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(state, fh)
        os.replace(tmp, p)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
