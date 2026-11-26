"""memogarden-docling trial ingest — /home/kureshii/Documents into MemoGarden.

The MemoGarden-aware half of the docling pipeline (the worker daemon in
memogarden-docling/ is content-agnostic). Idempotent by sha256; designed
to run every minute under a systemd timer and to catch up after downtime.

Two-phase scheduler (operator directive 2026-11-26: large documents are
deferred so they cannot clog the pipeline, and ingested during idle
periods; new documents always go first):

  Phase 1 (fresh)   — new PDFs in the watch root: sha256 → AttachmentStore
                      copy → `Attachment` fact (Triage, trial phase) → move
                      to the `ingested/` mirror → convert if small. Large
                      files (> DOCLING_MAX_PAGES) get their fact + mirror
                      slot and are DEFERRED.
  Phase 2 (heal)    — small facts whose Artifact is missing (crash/
                      worker-outage recovery): the STORE copy is what
                      converts, so the original file's position is
                      irrelevant.
  Phase 3 (idle)    — LARGE facts, converted back-to-back, but between any
                      two conversions the watch root is re-checked; if new
                      documents arrived, large-doc work PAUSES (deferred to
                      the next tick) so new documents never queue behind a
                      monster. The client timeout for a large conversion
                      scales with its page count so it can complete.

`Attachment` fact shape mirrors the gmail facts (filename, mime_type,
size, sha256, title, content_path) with metadata.provider="docling",
metadata.source="documents_watch", metadata.scopes=[Triage] (trial phase —
unclassified content, operator decision 2026-11-26).

`Artifact` entity: label/content/content_type per schema, data.scopes=
[Triage], data.source={attachment_uuid, sha256, origin_path} (the linkage:
artifact -> fact; reverse lookups query
json_extract(data,'$.source.attachment_uuid')), data.category="unknown"
(categorisation is a later slice), data.extraction={engine, version,
seconds, pages, truncated}.

canonical_at = the source file's mtime (documents have a real user time);
realized_at = now. Failures keep their fact and mirror slot and are
logged — retryable by hand; nothing is silently lost.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from system.core import get_core
from system.soil import get_soil
from system.soil.database import create_email_item

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
MAX_PAGES = int(os.environ.get("DOCLING_MAX_PAGES", "150"))
SMALL_TIMEOUT_S = int(os.environ.get("DOCLING_SOCK_TIMEOUT_S", "1800"))
LARGE_S_PER_PAGE = int(os.environ.get("DOCLING_LARGE_S_PER_PAGE", "90"))

OK, FAILED, DEFERRED, DUP = "ok", "failed", "deferred", "dup"


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[docling-ingest {ts}] {msg}", flush=True)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def page_count(pdf_path: Path) -> int | None:
    try:
        out = subprocess.run(
            ["pdfinfo", str(pdf_path)], capture_output=True, text=True, timeout=30
        ).stdout
        for line in out.splitlines():
            if line.startswith("Pages:"):
                return int(line.split()[-1])
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return None


def pending_new_pdfs() -> list[Path]:
    return sorted(
        p for p in WATCH_DIR.rglob("*.pdf")
        if p.is_file() and DONE_DIR not in p.parents
    )


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


def worker_convert(pdf_path: Path, timeout_s: int) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout_s)
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


def artifact_exists_for_fact(core, fact_uuid: str) -> str | None:
    row = core._get_conn().execute(
        """SELECT uuid FROM entity
           WHERE type = 'Artifact' AND superseded_by IS NULL
             AND json_extract(data, '$.source.attachment_uuid') = ?""",
        (fact_uuid,),
    ).fetchone()
    return row["uuid"] if row else None


def make_artifact(store_path: Path, fact_uuid: str, sha: str, rel: str,
                  filename: str, triage: str, large: bool = False
                  ) -> tuple[str, str]:
    """Convert via the worker and create the Core Artifact.

    Returns (OK | FAILED | DEFERRED, detail). DEFERRED = the document is
    over the page cap and this call is not an idle-phase slot — it does
    not touch the worker, so it can never clog the pipeline.
    """
    pages = page_count(store_path)
    if pages is not None and pages > MAX_PAGES and not large:
        return DEFERRED, f"{pages} pages > cap {MAX_PAGES} — deferred to idle"
    timeout_s = SMALL_TIMEOUT_S
    if large:
        # scale with page count so a monster conversion can complete
        timeout_s = max(SMALL_TIMEOUT_S, pages * LARGE_S_PER_PAGE + 600)
    try:
        resp = worker_convert(store_path, timeout_s)
    except (OSError, RuntimeError, ValueError) as e:
        return FAILED, f"worker unreachable: {e}"
    if resp.get("status") != "ok":
        return FAILED, f"convert failed: {str(resp.get('error', '?'))[:160]}"

    content = resp["markdown"]
    truncated = len(content) > MAX_CONTENT_CHARS
    if truncated:
        content = content[:MAX_CONTENT_CHARS]
    artifact_data = {
        "label": filename,
        "content": content,
        "content_type": "text/markdown",
        "scopes": [triage],
        "source": {"attachment_uuid": fact_uuid, "sha256": sha,
                   "origin_path": rel},
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
        artifact_uuid = core.entity.create(entity_type="Artifact",
                                           data=artifact_data)
        core._get_conn().commit()
    return OK, f"{artifact_uuid} ({resp['pages']}p, {resp['seconds']}s)"


def ingest_new_file(src: Path, triage: str) -> tuple[str, str | None, str, Path]:
    """Fresh-file path: fact + mirror, then convert if small.

    Returns (status, fact_uuid, sha, rel). status is OK (new fact) or DUP
    (content already ingested — fact_uuid is the existing fact's uuid).
    """
    rel = src.relative_to(WATCH_DIR)
    sha = sha256_of(src)
    canonical_at = datetime.fromtimestamp(
        src.stat().st_mtime, tz=timezone.utc
    ).isoformat()

    fact_uuid = None
    with get_soil() as soil:
        fact_uuid = fact_exists_for_sha(soil, sha)
        if not fact_uuid:
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
            fact_uuid = fact.uuid
    store_copy(src, sha)
    mirror_move(src, rel)
    return OK, fact_uuid, sha, rel


def missing_artifacts() -> tuple[list[dict], list[dict]]:
    """documents_watch facts without a live Artifact, split by page cap."""
    with get_soil() as soil:
        facts = soil._get_connection().execute(
            """SELECT uuid,
                      json_extract(data, '$.sha256') AS sha,
                      json_extract(data, '$.filename') AS filename,
                      json_extract(data, '$.content_path') AS content_path,
                      json_extract(metadata, '$.origin_path') AS origin_path
               FROM item
               WHERE _type = 'Attachment'
                 AND json_extract(metadata, '$.source') = 'documents_watch'"""
        ).fetchall()
    with get_core() as core:
        have = {
            r["uuid"]
            for r in core._get_conn().execute(
                """SELECT json_extract(data, '$.source.attachment_uuid') AS uuid
                   FROM entity
                   WHERE type = 'Artifact' AND superseded_by IS NULL
                     AND json_extract(data, '$.source.attachment_uuid') IS NOT NULL"""
            ).fetchall()
        }
    small: list[dict] = []
    large: list[dict] = []
    for row in facts:
        if row["uuid"] in have:
            continue
        store_path = STORE_ROOT / row["content_path"]
        if not store_path.exists():
            log(f"HEAL-SKIP {row['filename']} — store copy missing ({row['content_path']})")
            continue
        pages = page_count(store_path)
        f = dict(row)  # sqlite3.Row is immutable — copy before annotating
        f["store_path"] = store_path
        (large if (pages is not None and pages > MAX_PAGES) else small).append(f)
    return small, large


def main() -> int:
    DONE_DIR.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock = LOCK_FILE.open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("another ingest run holds the lock — exiting")
        return 0

    with get_core() as core:
        triage = triage_scope_uuid(core)
    if triage is None:
        log("FATAL: Triage / Untrusted scope not found in core — not ingesting")
        return 1

    processed = healed = dups = skipped = failed = deferred = 0

    # Phase 1 — fresh files: new documents ALWAYS go first
    candidates = pending_new_pdfs()
    if candidates:
        log(f"{len(candidates)} PDF(s) pending in {WATCH_DIR}")
    for src in candidates:
        try:
            if src.stat().st_size > MAX_SINGLE_FILE_BYTES:
                log(f"SKIP oversized {src.name}")
                skipped += 1
                continue
            if src.stat().st_size == 0:
                log(f"SKIP empty {src.name}")
                skipped += 1
                continue
            rel = src.relative_to(WATCH_DIR)
            status, fact_uuid, sha, rel = ingest_new_file(src, triage)
            if status == DUP:
                dups += 1
                continue
            store_path = STORE_ROOT / sha[0:2] / sha[2:4] / f"{sha}.pdf"
            pages = page_count(store_path)
            large = pages is not None and pages > MAX_PAGES
            if large:
                deferred += 1
                log(f"LARGE-DEFERRED {rel} ({pages}p) — fact + mirror done; "
                    "converts in an idle-phase slot")
                continue
            ok, detail = make_artifact(store_path, fact_uuid, sha,
                                       str(rel), src.name, triage)
            if ok == OK:
                processed += 1
                log(f"ARTIFACT {rel} -> {detail}")
            else:
                failed += 1
                log(f"ARTIFACT-FAILED {rel}: {detail} — fact kept, artifact deferred")
        except Exception as e:  # noqa: BLE001 — one bad file must not stop the run
            failed += 1
            log(f"ERROR {src}: {type(e).__name__}: {e}")

    # Phase 2 — heal small facts (crash/outage recovery)
    small_missing, large_missing = missing_artifacts()
    for f in small_missing:
        ok, detail = make_artifact(f["store_path"], f["uuid"], f["sha"],
                                   f["origin_path"] or f["filename"],
                                   f["filename"], triage)
        if ok == OK:
            healed += 1
            log(f"ARTIFACT(healed) {f['filename']} -> {detail}")
        else:
            failed += 1
            log(f"ARTIFACT-FAILED {f['filename']}: {detail}")

    # Phase 3 — idle tail: LARGE documents, preemptible by new arrivals
    if large_missing:
        log(f"IDLE: {len(large_missing)} large document(s) queued "
            f"(> {MAX_PAGES} pages); pausing if new files arrive")
        for f in large_missing:
            fresh = pending_new_pdfs()
            if fresh:
                log(f"IDLE-PAUSED: {len(fresh)} new document(s) arrived — "
                    "large queue deferred to the next tick")
                break
            ok, detail = make_artifact(f["store_path"], f["uuid"], f["sha"],
                                       f["origin_path"] or f["filename"],
                                       f["filename"], triage, large=True)
            if ok == OK:
                processed += 1
                log(f"ARTIFACT(large) {f['filename']} -> {detail}")
            else:
                failed += 1
                log(f"ARTIFACT-FAILED(large) {f['filename']}: {detail}")

    log(f"done: {processed} artifacts ({healed} healed), {dups} dups, "
        f"{skipped} skipped, {deferred} deferred, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
