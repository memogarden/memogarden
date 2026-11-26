"""memogarden-docling trial ingest — /home/kureshii/Documents into MemoGarden.

The MemoGarden-aware half of the docling pipeline (the worker daemon in
memogarden-docling/ is content-agnostic). Idempotent by sha256; designed
to run every minute under a systemd timer and to catch up after downtime.

Per new PDF (relative to the watch root, `ingested/` subtree skipped):
  1. sha256; content-addressed copy into the AttachmentStore (same layout
     as gmail attachments: <store>/<h2>/<h4>/<sha256>.pdf)
  2. `Attachment` fact in Soil — gmail fact shape (filename, mime_type,
     size, sha256, title, content_path) with metadata.provider="docling",
     metadata.source="documents_watch"; scope: Triage (trial phase —
     unclassified content, operator decision 2026-11-26)
  3. file moved to the `ingested/` mirror (relative path preserved)
  4. Docling worker (unix socket) converts the STORE copy
  5. `Artifact` entity in Core — label/content/content_type per schema,
     data.scopes=[Triage], data.source={attachment_uuid, sha256,
     origin_path} (the memogarden linkage: artifact -> fact; reverse
     lookups query json_extract(data,'$.source.attachment_uuid')),
     data.category="unknown" (categorisation is a later slice),
     data.extraction={engine, version, seconds, pages}

Canonical time = the source file's mtime (documents have a real user
time); realized_at = now. A file that fails conversion keeps its fact and
its place in the mirror, and the failure is logged — retryable by hand;
nothing is silently lost. Duplicate content (a file whose sha256 was
already ingested from Documents) is moved to the mirror and skipped.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
WATCH_DIR = Path(os.environ.get("DOCLING_WATCH_DIR", HOME / "Documents"))
DONE_DIR = Path(os.environ.get("DOCLING_DONE_DIR", WATCH_DIR / "ingested"))
SOCK = Path(
    os.environ.get(
        "DOCLING_SOCK",
        os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        + "/memogarden-docling/docling.sock",
    )
)
STORE_ROOT = Path(
    os.environ.get(
        "MEMOGARDEN_ATTACHMENT_STORE", HOME / ".local/share/memogarden/attachments"
    )
)
LOCK_FILE = HOME / ".local/share/memogarden/docling/ingest.lock"
MAX_CONTENT_CHARS = int(os.environ.get("DOCLING_MAX_CONTENT_CHARS", "400000"))
MAX_SINGLE_FILE_BYTES = 100 * 1024 * 1024

DOCLING_SOCK_TIMEOUT_S = 1800


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[docling-ingest {ts}] {msg}", flush=True)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def triage_scope_uuid(core) -> str | None:
    row = core._get_conn().execute(
        """SELECT uuid FROM entity
           WHERE type = 'Scope' AND superseded_by IS NULL
             AND json_extract(data, '$.label') = 'Triage / Untrusted'"""
    ).fetchone()
    return row["uuid"] if row else None


def fact_exists_for_sha(soil, sha: str) -> str | None:
    row = soil._get_connection().execute(
        """SELECT uuid FROM item
           WHERE _type = 'Attachment'
             AND json_extract(data, '$.sha256') = ?
             AND json_extract(metadata, '$.source') = 'documents_watch'""",
        (sha,),
    ).fetchone()
    return row["uuid"] if row else None


def worker_convert(pdf_path: Path) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(DOCLING_SOCK_TIMEOUT_S)
        s.connect(str(SOCK))
        s.sendall((json.dumps({"path": str(pdf_path)}) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(1 << 20)
            if not chunk:
                raise RuntimeError("worker closed the connection mid-response")
            buf += chunk
    return json.loads(buf.decode())


def store_copy(src: Path, sha: str) -> Path:
    dest = STORE_ROOT / sha[0:2] / sha[2:4] / f"{sha}.pdf"
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    return dest


def mirror_move(src: Path, rel: Path) -> Path:
    dest = DONE_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        n = 1
        while dest.exists():
            dest = dest.with_name(f"{stem}-{n}{suffix}")
            n += 1
    shutil.move(str(src), str(dest))
    return dest


def main() -> int:
    DONE_DIR.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock = LOCK_FILE.open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("another ingest run holds the lock — exiting")
        return 0

    from system.core import get_core
    from system.soil import get_soil
    from system.soil.database import create_email_item

    candidates = sorted(
        p for p in WATCH_DIR.rglob("*.pdf")
        if p.is_file() and DONE_DIR not in p.parents
    )
    if not candidates:
        return 0
    log(f"{len(candidates)} PDF(s) pending in {WATCH_DIR}")

    with get_core() as core:
        triage = triage_scope_uuid(core)
    if triage is None:
        log("FATAL: Triage / Untrusted scope not found in core — not ingesting")
        return 1

    processed = dups = skipped = failed = 0
    for src in candidates:
        try:
            rel = src.relative_to(WATCH_DIR)
            if src.stat().st_size > MAX_SINGLE_FILE_BYTES:
                log(f"SKIP oversized {rel}")
                skipped += 1
                continue
            if src.stat().st_size == 0:
                log(f"SKIP empty {rel}")
                skipped += 1
                continue
            sha = sha256_of(src)
            canonical_at = datetime.fromtimestamp(
                src.stat().st_mtime, tz=timezone.utc
            ).isoformat()

            existing = None
            with get_soil() as soil:
                existing = fact_exists_for_sha(soil, sha)
                if not existing:
                    fact = create_email_item(
                        _type="Attachment",
                        realized_at=datetime.now(timezone.utc).isoformat(),
                        canonical_at=canonical_at,
                        fidelity="full",
                        data={
                            "filename": src.name,
                            "mime_type": "application/pdf",
                            "size": src.stat().st_size,
                            "sha256": sha,
                            "title": src.stem,
                            "content_path": f"{sha[0:2]}/{sha[2:4]}/{sha}.pdf",
                        },
                        metadata={
                            "provider": "docling",
                            "source": "documents_watch",
                            "storage_root": str(STORE_ROOT),
                            "fetched_at": datetime.now(timezone.utc).isoformat(),
                            "origin_path": str(rel),
                            "scopes": [triage],
                        },
                    )
                    soil.create_fact(fact)
            store_path = store_copy(src, sha)
            moved = mirror_move(src, rel)

            if existing:
                dups += 1
                log(f"DUP {rel} — content already ingested ({existing[:18]}…)")
                continue

            log(f"FACT {rel} -> {fact.uuid[:22]}… (store {sha[:12]}…)")

            try:
                resp = worker_convert(store_path)
            except (OSError, RuntimeError, ValueError) as e:
                log(f"WORKER-UNREACHABLE {rel}: {e} — fact kept, artifact deferred")
                failed += 1
                continue
            if resp.get("status") != "ok":
                log(f"CONVERT-FAILED {rel}: {str(resp.get('error', '?'))[:160]} — fact kept, artifact deferred")
                failed += 1
                continue

            content = resp["markdown"]
            truncated = len(content) > MAX_CONTENT_CHARS
            if truncated:
                content = content[:MAX_CONTENT_CHARS]
            artifact_data = {
                "label": src.name,
                "content": content,
                "content_type": "text/markdown",
                "scopes": [triage],
                "source": {"attachment_uuid": fact.uuid, "sha256": sha,
                           "origin_path": str(rel)},
                "category": "unknown",
                "extraction": {
                    "engine": resp["engine"]["name"],
                    "version": resp["engine"]["version"],
                    "seconds": resp["seconds"],
                    "pages": resp["pages"],
                    "truncated": truncated,
                },
            }
            with get_core() as core:
                artifact_uuid = core.entities.create("Artifact", data=artifact_data)
                core._get_conn().commit()
            processed += 1
            log(f"ARTIFACT {rel} -> {artifact_uuid[:22]}… ({resp['pages']}p, {resp['seconds']}s)")
        except Exception as e:  # noqa: BLE001 — one bad file must not stop the run
            failed += 1
            log(f"ERROR {src}: {type(e).__name__}: {e}")

    log(f"done: {processed} artifacts, {dups} dups, {skipped} skipped, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
