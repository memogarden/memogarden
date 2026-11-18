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
import json
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from system.soil import Soil
from system.soil.fact import Fact, generate_soil_uuid
from system.soil.relation import SystemRelation

from gmail_importer.gmail_api import GmailApiError

from gmail_importer.importer import GmailImporter

# The content-addressed store is shared infrastructure now (the RFC-013
# sessions drain stores browser downloads in the SAME residence):
# promoted to memogarden-utils; re-exported here for existing callers.
from utils.attachments import AttachmentStore, safe_ext as _safe_ext

logger = logging.getLogger(__name__)

# Same convention as the mbox importer's relation timestamps.
_RELATION_EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _days_since_epoch() -> int:
    return (datetime.now(timezone.utc) - _RELATION_EPOCH).days


class AttachmentExtractor:
    """Download Gmail attachments for ingested emails into an AttachmentStore.

    For every Email fact that declares attachments, the Gmail message is
    refetched by its stored gmail_message_id (facts predate attachment-id
    metadata), each attachment part is downloaded, and an Attachment fact +
    `contained_by` relation are created. Re-runs skip instances already
    extracted, keyed on (email uuid, attachment filename) — Gmail's
    attachment ids are per-fetch handles that change between fetches.

    Account routing (RFC-012 §4.6): the refetch goes through the client of
    the account that OWNS the email fact (its metadata.account) — fetching
    account B's mail with account A's token 404s and would masquerade as a
    deletion. `api_factory(account_subject)` is called lazily per account,
    only when an uncovered email forces a refetch; None-keyed legacy facts
    (pre-attribution) map to the caller's default account.
    """

    def __init__(
        self,
        soil: Soil,
        store: AttachmentStore,
        api_factory: Callable[[Optional[str]], object],
        verbose: bool = True,
        default_account: Optional[str] = None,
    ):
        self.soil = soil
        self.store = store
        self._api_factory = api_factory
        self.verbose = verbose
        self._default_account = default_account
        self._clients: dict[Optional[str], object] = {}
        self._dead_accounts: set[Optional[str]] = set()
        self.stats = {
            "emails_scanned": 0,
            "emails_covered": 0,
            "emails_deleted": 0,
            "attachments_found": 0,
            "downloaded": 0,
            "attachments_missing": 0,
            "skipped_existing": 0,
            "auth_skipped": 0,
            "errors": 0,
        }

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[attachments] {msg}")

    def _client_for(self, account: Optional[str]):
        """The API client owning this email's mailbox — lazily per account.

        A credential-shaped failure parks the account for the rest of the
        pass (its token is dead; retrying per email would only spam) —
        mirror of the runtime's needs_reauth behavior for the CLI pass.
        """
        subject = account or self._default_account
        if subject in self._dead_accounts:
            raise GmailApiError(
                f"account {subject} failed credential refresh earlier this pass",
                auth_failure=True,
            )
        if subject not in self._clients:
            try:
                self._clients[subject] = self._api_factory(subject)
            except GmailApiError as e:
                if e.auth_failure:
                    self._dead_accounts.add(subject)
                    self._log(
                        f"account {subject}: credential refresh failed — "
                        "skipping its emails (re-link the account to recover)"
                    )
                raise
        return self._clients[subject]

    def _existing_index(self) -> dict[tuple[str, str], str]:
        """(email uuid, attachment filename) -> Attachment fact uuid.

        NB: Gmail attachment IDs are NOT stable — the same message's
        attachment gets a different id on every fetch — so they identify a
        download session, never an extracted instance. Filename is stable.

        Projected in SQL (only the three keys are read): fact-level
        materialization of the whole corpus peaked an hourly no-op run at
        ~1.6 GB.
        """
        rows = self.soil._get_connection().execute(
            """SELECT uuid,
                      json_extract(metadata, '$.email_uuid') AS email_uuid,
                      json_extract(data, '$.filename') AS filename
               FROM item
               WHERE _type = 'Attachment'"""
        ).fetchall()
        return {
            (row["email_uuid"], row["filename"]): row["uuid"]
            for row in rows
            if row["email_uuid"] and row["filename"]
        }

    def extract_all(self, dry_run: bool = False, account_filter: Optional[str] = None) -> dict:
        # Clients are built lazily per account (see _client_for): a
        # covered-only pass (the steady-state hourly case) skips both the
        # KDF and the network entirely.
        # Everything (index build, listing, facts) happens inside one Soil
        # context; the API calls are slow but the connection tolerates it.
        with self.soil:
            existing = self._existing_index()
            # Candidate scan is a projected query (uuid, gmail id, owning
            # account, declared filenames); full Facts are fetched only for
            # uncovered emails.
            rows = self.soil._get_connection().execute(
                """SELECT uuid,
                          json_extract(metadata, '$.gmail_message_id') AS gmail_id,
                          json_extract(metadata, '$.account') AS account,
                          json_extract(data, '$.attachment_filenames') AS filenames
                   FROM item
                   WHERE _type = 'Email'
                     AND json_extract(data, '$.attachment_count') > 0
                     AND json_extract(metadata, '$.gmail_message_id') IS NOT NULL"""
            ).fetchall()
            emails = []
            for row in rows:
                account = row["account"]
                if account_filter is not None:
                    # --account mode: only emails this account owns (legacy
                    # unattributed facts ride on the default account).
                    if (account or self._default_account) != account_filter:
                        continue
                emails.append(
                    (
                        row["uuid"],
                        row["gmail_id"],
                        account,
                        json.loads(row["filenames"]) if row["filenames"] else [],
                    )
                )
            self._log(f"{len(emails)} ingested emails declare attachments")

            for email_uuid, gmail_id, account, declared in emails:
                self.stats["emails_scanned"] += 1
                # Coverage skip: if every filename the email fact declares is
                # already extracted, refetching the message is pointless —
                # its attachment set cannot have grown (mail is immutable).
                # This keeps the pass O(new mail) instead of O(all mail with
                # attachments) as the mailbox grows.
                if declared and all(
                    (email_uuid, fn) in existing for fn in declared
                ):
                    self.stats["emails_covered"] += 1
                    continue
                email = self.soil.get_fact(email_uuid)
                if email is None:
                    self.stats["errors"] += 1
                    logger.warning("Email %s vanished mid-scan; skipping attachments", email_uuid)
                    continue
                try:
                    api = self._client_for(account)
                    message = api.get_message(gmail_id)
                except GmailApiError as e:
                    if e.auth_failure:
                        # Account parked by _client_for (or a mid-run 401):
                        # its remaining emails are skipped, not errors.
                        self.stats["auth_skipped"] += 1
                        continue
                    if e.status_code == 404:
                        # message deleted at the source since ingestion — its
                        # already-extracted attachments remain valid history
                        self.stats["emails_deleted"] += 1
                        logger.info("Email %s deleted at source; skipping attachments", email.uuid)
                        continue
                    self.stats["errors"] += 1
                    logger.warning("Refetch failed for %s: %s", email.uuid, e)
                    continue
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
                    except GmailApiError as e:
                        if e.status_code == 404:
                            self.stats["attachments_missing"] += 1
                            logger.warning("Attachment %s/%s deleted at source", email.uuid, filename)
                        else:
                            self.stats["errors"] += 1
                            logger.warning("Extraction failed for %s/%s: %s", email.uuid, filename, e)
                    except Exception as e:
                        self.stats["errors"] += 1
                        logger.warning("Extraction failed for %s/%s: %s", email.uuid, filename, e)
                # RFC-012 §4.9: commit per email, not once per pass. The whole-
                # pass transaction held soil.db's single writer lock for the
                # entire extraction (15-30 min with a real backlog), starving
                # concurrent account runs into "database is locked" failures
                # (observed live: alt lost four consecutive heartbeats). Short
                # bursts interleave under the 5s busy_timeout; partial progress
                # is also crash-durable now.
                self.soil.commit()
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
                # RFC-012 §4.7: inherit the owning email's account
                "account": email.metadata.get("account"),
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
