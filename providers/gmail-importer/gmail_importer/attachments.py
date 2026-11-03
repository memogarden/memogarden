"""Content-addressed attachment storage + Attachment facts.

Binaries live under <data_dir>/attachments/<h2>/<h2>/<sha256><ext>: the
file is named by its own content hash, so identical files deduplicate and
any copy can be integrity-checked against the fact's sha256.

Each (email, attachment) instance becomes an immutable Attachment fact
linked to its email with a `contained_by` relation. Search indexes the
fact metadata (original filename, mime, size, sha256, gmail ids); the
email facts themselves are never modified and Gmail stays read-only.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from system.soil import Soil
from system.soil.fact import Fact, generate_soil_uuid
from system.soil.relation import SystemRelation

from gmail_importer.importer import GmailImporter

logger = logging.getLogger(__name__)

# Same convention as the mbox importer's relation timestamps.
_RELATION_EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _days_since_epoch() -> int:
    return (datetime.now(timezone.utc) - _RELATION_EPOCH).days


def _safe_ext(filename: str) -> str:
    """Filesystem-safe extension from the original filename ('' if none)."""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    ext = name.rsplit(".", 1)[1]
    cleaned = "".join(c for c in ext if c.isalnum())
    if not cleaned:
        return ""
    return "." + cleaned[:10]


class AttachmentStore:
    """Content-addressed binary store rooted at a directory on disk."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def path_for(self, digest: str, ext: str = "") -> Path:
        return self.root / digest[:2] / digest[2:4] / f"{digest}{ext}"

    def write(self, data: bytes, original_filename: str) -> tuple[str, str]:
        """Write bytes; returns (path relative to root, sha256 hex digest).

        Idempotent: content already present is left untouched (same hash
        means the same bytes).
        """
        digest = hashlib.sha256(data).hexdigest()
        path = self.path_for(digest, _safe_ext(original_filename))
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.parent / (path.name + ".part")
            tmp.write_bytes(data)
            tmp.rename(path)
        return str(path.relative_to(self.root)), digest


class AttachmentExtractor:
    """Download Gmail attachments for ingested emails into an AttachmentStore.

    For every Email fact that declares attachments, the Gmail message is
    refetched by its stored gmail_message_id (facts predate attachment-id
    metadata), each attachment part is downloaded, and an Attachment fact +
    `contained_by` relation are created. Re-runs skip instances already
    extracted, keyed on (email uuid, attachment filename) — Gmail's
    attachment ids are per-fetch handles that change between fetches.
    """

    def __init__(
        self,
        soil: Soil,
        store: AttachmentStore,
        api_factory: Callable[[], object],
        verbose: bool = True,
    ):
        self.soil = soil
        self.store = store
        self._api_factory = api_factory
        self.verbose = verbose
        self.stats = {
            "emails_scanned": 0,
            "emails_covered": 0,
            "attachments_found": 0,
            "downloaded": 0,
            "skipped_existing": 0,
            "errors": 0,
        }

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[attachments] {msg}")

    def _existing_index(self) -> dict[tuple[str, str], str]:
        """(email uuid, attachment filename) -> Attachment fact uuid.

        NB: Gmail attachment IDs are NOT stable — the same message's
        attachment gets a different id on every fetch — so they identify a
        download session, never an extracted instance. Filename is stable.
        """
        index: dict[tuple[str, str], str] = {}
        for fact in self.soil.list_items(_type="Attachment", limit=1_000_000):
            email_uuid = (fact.metadata or {}).get("email_uuid")
            filename = (fact.data or {}).get("filename")
            if email_uuid and filename:
                index[(email_uuid, filename)] = fact.uuid
        return index

    def extract_all(self, dry_run: bool = False) -> dict:
        api = self._api_factory()
        # Everything (index build, listing, facts) happens inside one Soil
        # context; the API calls are slow but the connection tolerates it.
        with self.soil:
            existing = self._existing_index()
            emails = [
                f
                for f in self.soil.list_items(_type="Email", limit=1_000_000)
                if f.data.get("attachment_count") and f.metadata.get("gmail_message_id")
            ]
            self._log(f"{len(emails)} ingested emails declare attachments")

            for email in emails:
                self.stats["emails_scanned"] += 1
                # Coverage skip: if every filename the email fact declares is
                # already extracted, refetching the message is pointless —
                # its attachment set cannot have grown (mail is immutable).
                # This keeps the pass O(new mail) instead of O(all mail with
                # attachments) as the mailbox grows.
                declared = email.data.get("attachment_filenames") or []
                if declared and all(
                    (email.uuid, fn) in existing for fn in declared
                ):
                    self.stats["emails_covered"] += 1
                    continue
                try:
                    message = api.get_message(email.metadata["gmail_message_id"])
                except Exception as e:
                    self.stats["errors"] += 1
                    logger.warning("Refetch failed for %s: %s", email.uuid, e)
                    continue
                for part in GmailImporter._walk_parts(message.get("payload", {})):
                    part_headers = GmailImporter._part_headers(part)
                    disposition = part_headers.get("content-disposition", "")
                    filename = part.get("filename") or ""
                    attachment_id = part.get("body", {}).get("attachmentId")
                    if not (filename and "attachment" in disposition.lower() and attachment_id):
                        continue
                    self.stats["attachments_found"] += 1
                    if (email.uuid, filename) in existing:
                        self.stats["skipped_existing"] += 1
                        continue
                    self._log(f"fetching {filename} ({attachment_id}) for {email.uuid}")
                    if dry_run:
                        continue
                    try:
                        self._extract_one(api, email, attachment_id, filename, part)
                        existing[(email.uuid, filename)] = "new"
                        self.stats["downloaded"] += 1
                    except Exception as e:
                        self.stats["errors"] += 1
                        logger.warning("Extraction failed for %s/%s: %s", email.uuid, filename, e)
        return self.stats

    def _extract_one(
        self, api, email: Fact, attachment_id: str, filename: str, part: dict
    ) -> Fact:
        # NB: the Attachment resource puts `data` at the TOP LEVEL (not under
        # `body`) — verified against the live API.
        response = api.get_attachment(email.metadata["gmail_message_id"], attachment_id)
        body_data = response.get("data", "")
        data = base64.urlsafe_b64decode(body_data + "=" * (-len(body_data) % 4))
        if not data:
            raise ValueError(f"empty payload for {filename} ({attachment_id})")

        declared_size = part.get("body", {}).get("size")
        if declared_size is not None and int(declared_size) != len(data):
            logger.warning(
                "Size mismatch for %s (declared %s, got %s) — storing anyway",
                filename, declared_size, len(data),
            )

        rel_path, digest = self.store.write(data, filename)
        now = datetime.now(timezone.utc).isoformat()
        fact = Fact(
            uuid=generate_soil_uuid(),
            _type="Attachment",
            realized_at=now,
            canonical_at=email.canonical_at,  # arrived with its email
            fidelity="full",
            data={
                "filename": filename,
                "mime_type": part.get("mimeType", ""),
                "size": len(data),
                "sha256": digest,
                "title": filename,  # searchable like other facts
                "content_path": rel_path,
            },
            metadata={
                "provider": "google",
                "source": "gmail_api",
                "email_uuid": email.uuid,
                "gmail_message_id": email.metadata.get("gmail_message_id"),
                "gmail_attachment_id": attachment_id,
                "storage_root": str(self.store.root),
                "fetched_at": now,
            },
        )
        self.soil.create_fact(fact)

        relation = SystemRelation(
            uuid=generate_soil_uuid(),
            kind="contained_by",
            source=fact.uuid,
            source_type="item",
            target=email.uuid,
            target_type="item",
            created_at=_days_since_epoch(),
            evidence=None,
            metadata=None,
        )
        self.soil.create_relation(relation)
        self._log(f"stored {filename} -> {rel_path} (sha256 {digest[:12]}...)")
        return fact
