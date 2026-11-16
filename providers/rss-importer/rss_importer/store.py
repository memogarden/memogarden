"""Content-addressed artifact store for archived feed responses and
full-fetched articles.

Mirrors the gmail AttachmentStore layout exactly —
`<artifacts_dir>/<h2>/<h2>/<sha256><ext>` — so identical bytes dedup across
providers (an unchanged feed archived twice lands in one file). Follows the
"reference code, don't copy it" preference by reusing the *convention*, not
the gmail module (importing across provider packages would couple them);
if the layout logic is ever needed by a third provider, promote it to
memogarden-utils (the same open question the sessions-provider proposal
carries).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

DEFAULT_ARTIFACTS_DIR = "~/.local/share/memogarden/attachments"


def store_bytes(artifacts_dir: str | os.PathLike, content: bytes, ext: str = "") -> dict:
    """Write `content` into the content-addressed layout and return the
    fact-data payload fields ({sha256, size, content_path}). Idempotent:
    existing files are left untouched (same hash = same bytes)."""
    digest = hashlib.sha256(content).hexdigest()
    directory = (
        Path(artifacts_dir).expanduser() / digest[:2] / digest[2:4]
    )
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{digest}{ext}"
    if not path.exists():
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(content)
        tmp.replace(path)
    return {
        "sha256": digest,
        "size": len(content),
        "content_path": str(path),
    }
