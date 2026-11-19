"""Drain worker for the memogarden-sessions staging cache (RFC-013 §8).

The sessions daemon captures downloads and screenshots into per-session
staging dirs (<sessions_root>/<name>/{downloads,screenshots}) and appends
fact-shaped events (§12.1 control/lifecycle vocabulary + page_visited)
to events.jsonl. This provider drains BOTH:

- staged files -> gmail-parity Attachment facts in MemoGarden's shared
  content-addressed store, then the staged copy is deleted. The cache is
  a STAGING AREA, not a residence: steady state is drained and
  near-empty (register D-3: zero local retention post-drain; undrained
  captures are never evicted).
- event lines -> SessionEvent facts (the §12.1 mirror incl. refusals,
  register C4) and PageVisit facts (per-navigation history, C3).

Drain semantics (register B2/B3/B6 + C3/C4):
- Presence-as-queue: a staged file present = pending; gone = drained.
- Ack-after-commit: a file is deleted only AFTER its fact is durably
  committed — deletion happens when the generator is re-entered for the
  next item (the runtime commits per yielded fact), or after the loop
  completes normally. A crash mid-run leaves the file staged; the
  content-hash dedup makes the retry a no-op fact-wise.
- At-least-once is harmless: `already_drained` checks (session, sha256)
  before yielding; events probe (session, seq, ts). Events are NOT
  watermarked (deviation from B4 noted in the register): legacy event
  files contain duplicate seq values across pre-fix daemon restarts, so
  the drain re-reads each file and probes every line — cheap at this
  scale, and exact for both legacy and new files.
- download_captured is FILTERED (D2): it folds into the Attachment
  fact's metadata and never becomes its own fact.

Provenance (register C1/D2): the download_captured event carries
url/captured_at/downloaded_by/size/content_type; the drain folds it into
the Attachment fact's metadata. Files captured before provenance existed
(mtime-only) drain with captured_at from the file and url=None.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from system.soil import get_soil
from utils.attachments import AttachmentStore

logger = logging.getLogger(__name__)

# In-flight browser downloads — never drained.
PARTIAL_SUFFIXES = (".crdownload", ".part", ".tmp")

# Staged artifact dirs that drain, and their metadata.stream values.
STREAM_DIRS = (("downloads", "downloads"), ("screenshots", "screenshots"))

# D2: folded into Attachment metadata — never mirrored as a fact.
EVENTS_SKIP = ("download_captured",)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


class SessionsImporter:
    """Provider contract: cls(config) + sync(since) -> yields fact dicts.

    Config keys (registry config):
    - sessions_root: staging cache root (default: the daemon's default).
    - store_root:    content-addressed store (default: the shared
                     <data_dir>/attachments residence gmail uses).
    """

    def __init__(self, config: dict):
        config = config or {}
        self.root = Path(
            config.get("sessions_root")
            or Path.home() / ".local" / "share" / "memogarden" / "sessions"
        )
        self.store_root = Path(
            config.get("store_root")
            or Path.home() / ".local" / "share" / "memogarden" / "attachments"
        )
        self._soil_factory = get_soil  # tests swap this seam

    # ------------------------------------------------------------------ sync

    def sync(self, since=None):
        """Yield facts for every staged artifact and every undrained
        event line (see module docstring). `since` is ignored:
        presence-as-queue + dedup probes are the cursor."""
        store = AttachmentStore(self.store_root)
        soil = self._soil_factory()
        pending: Path | None = None

        with soil:
            for sdir in self._session_dirs():
                manifest = self._manifest(sdir)
                if manifest is None:
                    continue
                events = self._capture_events(sdir)
                for stream, sdir_path in self._stream_dirs(sdir):
                    for staged in sorted(sdir_path.iterdir()):
                        if not staged.is_file():
                            continue
                        if staged.name.endswith(PARTIAL_SUFFIXES):
                            continue
                        digest = self._sha256(staged)
                        if self._already_drained(soil, manifest["name"], digest):
                            self._discard(staged)
                            continue
                        data = staged.read_bytes()
                        rel_path, store_digest = store.write(data, staged.name)
                        fact = self._fact(
                            manifest, staged, stream, events,
                            data=data, digest=store_digest, rel_path=rel_path,
                        )
                        yield fact
                        # Resuming here means the runtime has committed the
                        # yielded fact: the PREVIOUS staged file is now
                        # redundant, and this one becomes the pending ack.
                        if pending is not None:
                            self._discard(pending)
                        pending = staged
                yield from self._drain_events(soil, manifest, sdir)
        if pending is not None:
            # Normal completion: the last fact was committed before the
            # runtime asked for the item after it (StopIteration).
            self._discard(pending)

    def _drain_events(self, soil, manifest: dict, sdir: Path):
        """Mirror events.jsonl into SessionEvent / PageVisit facts.

        Re-read per run and probed per line (see module docstring) — no
        watermark, exact for legacy files whose seq values reset across
        pre-fix daemon restarts."""
        log = Path(sdir) / "events.jsonl"
        name = manifest["name"]
        if not log.is_file():
            return
        try:
            with log.open() as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    event = rec.get("event")
                    seq, ts = rec.get("seq"), rec.get("ts")
                    if not event or seq is None or ts is None:
                        continue
                    if event in EVENTS_SKIP:
                        continue
                    if self._event_drained(soil, name, seq, ts):
                        continue
                    if event == "page_visited":
                        yield self._visit_fact(manifest, rec)
                    else:
                        yield self._event_fact(manifest, rec)
        except OSError as e:
            logger.warning("could not read events for %s: %s", name, e)

    def _event_fact(self, manifest: dict, rec: dict) -> dict:
        """§12.1 control/lifecycle mirror (register C4): one SessionEvent
        fact kind, the envelope as data (event = discriminator), actor
        mapped server-side later via RFC-010 entities."""
        return {
            "type": "SessionEvent",
            "canonical_at": _iso(rec["ts"]),
            "data": rec,
            "metadata": {
                "provider": "memogarden-sessions",
                "source": "browser_session",
                "stream": "events",
                "session": manifest["name"],
                "machine": manifest.get("machine"),
                "actor": rec.get("actor"),
                "drained_at": _iso(time.time()),
            },
        }

    def _visit_fact(self, manifest: dict, rec: dict) -> dict:
        """Per-navigation history (register C3): generically shaped
        PageVisit (url/title/ts + metadata.source discipline) so other
        sources (e.g. readly reads) can subsume the kind later."""
        visited_at = rec.get("visited_at") or _iso(rec["ts"])
        return {
            "type": "PageVisit",
            "canonical_at": visited_at,
            "data": {
                "url": rec.get("url"),
                "title": rec.get("title"),
                "visited_at": visited_at,
                "seq": rec.get("seq"),
                "ts": rec.get("ts"),
                "actor": rec.get("actor"),
            },
            "metadata": {
                "provider": "memogarden-sessions",
                "source": "browser_session",
                "stream": "history",
                "session": manifest["name"],
                "machine": manifest.get("machine"),
                "account": manifest["name"],
                "drained_at": _iso(time.time()),
            },
        }

    @staticmethod
    def _event_drained(soil, session: str, seq, ts) -> bool:
        """(session, seq, ts) probe — ts disambiguates legacy files where
        seq reset across daemon restarts (B3, legacy-safe)."""
        row = soil._get_connection().execute(
            """SELECT 1 FROM item
               WHERE _type IN ('SessionEvent', 'PageVisit')
                 AND json_extract(metadata, '$.session') = ?
                 AND json_extract(data, '$.seq') = ?
                 AND json_extract(data, '$.ts') = ?
               LIMIT 1""",
            (session, seq, ts),
        ).fetchone()
        return row is not None

    # --------------------------------------------------------------- helpers

    def _session_dirs(self) -> list[Path]:
        if not self.root.exists():
            return []
        return [d for d in sorted(self.root.iterdir())
                if (d / "session.json").is_file()]

    @staticmethod
    def _manifest(sdir: Path) -> dict | None:
        try:
            manifest = json.loads((sdir / "session.json").read_text())
        except (OSError, ValueError):
            logger.warning("unreadable manifest in %s; skipping", sdir)
            return None
        if not manifest.get("name"):
            return None
        return manifest

    @staticmethod
    def _stream_dirs(sdir: Path):
        for dirname, stream in STREAM_DIRS:
            path = sdir / dirname
            if path.is_dir():
                yield stream, path

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _capture_events(sdir: Path) -> dict[str, dict]:
        """filename -> latest download_captured event (provenance source)."""
        log = sdir / "events.jsonl"
        out: dict[str, dict] = {}
        if not log.is_file():
            return out
        try:
            with log.open() as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if rec.get("event") == "download_captured" and rec.get("filename"):
                        out[rec["filename"]] = rec
        except OSError:
            pass
        return out

    @staticmethod
    def _already_drained(soil, session: str, digest: str) -> bool:
        """(session, sha256) instance already extracted? (register B3 —
        makes at-least-once replay harmless.)"""
        row = soil._get_connection().execute(
            """SELECT uuid FROM item
               WHERE _type = 'Attachment'
                 AND json_extract(metadata, '$.session') = ?
                 AND json_extract(data, '$.sha256') = ?
               LIMIT 1""",
            (session, digest),
        ).fetchone()
        return row is not None

    def _fact(
        self, manifest: dict, staged: Path, stream: str,
        events: dict[str, dict], *, data: bytes, digest: str, rel_path: str,
    ) -> dict:
        name = manifest["name"]
        event = events.get(staged.name)
        captured_at = (event or {}).get("captured_at") or _iso(staged.stat().st_mtime)
        now = _iso(time.time())
        mime = (event or {}).get("content_type") or _guess_mime(staged.name)
        return {
            "type": "Attachment",
            "canonical_at": captured_at,
            "data": {
                "filename": staged.name,
                "mime_type": mime,
                "size": len(data),
                "sha256": digest,
                "title": staged.name,  # searchable like other facts
                "content_path": rel_path,
            },
            "metadata": {
                "provider": "memogarden-sessions",
                "source": "browser_session",
                "stream": stream,
                "session": name,
                "machine": manifest.get("machine"),
                # RFC-012 attribution: the session IS the account handle;
                # scope rules and triage relabels key on this.
                "account": name,
                "url": (event or {}).get("url"),
                "downloaded_by": (event or {}).get("downloaded_by", "unknown"),
                "captured_at": captured_at,
                "storage_root": str(self.store_root),
                "drained_at": now,
            },
        }

    @staticmethod
    def _discard(staged: Path) -> None:
        """Delete a drained staged file; prune the dir if it empties.
        Register F2: zero local retention post-drain."""
        try:
            staged.unlink(missing_ok=True)
            parent = staged.parent
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError as e:
            logger.warning("could not remove drained staging file %s: %s",
                           staged, e)


def _guess_mime(filename: str) -> str:
    import mimetypes

    return mimetypes.guess_type(filename)[0] or "application/octet-stream"
