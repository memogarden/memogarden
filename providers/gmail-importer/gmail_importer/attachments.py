"""Content-addressed attachment storage + Attachment planning.

Binaries live under <data_dir>/attachments/<h2>/<h2>/<sha256><ext>: the
file is named by its own content hash, so identical files deduplicate and
any copy can be integrity-checked against the fact's sha256.

Attachment facts are created by the RUNTIME (providers yield, MemoGarden
stores — RFC-012 §4.9: binaries joined the provider contract 2026-11-20).
This module only PREPARES attachments: for every Email fact that declares
attachments, the Gmail message is refetched by its stored gmail_message_id
(facts predate attachment-id metadata), each attachment part is downloaded
into the store, and a runtime-normalized dict is yielded. The runtime then
creates the fact (applying the operator's scope policy, RFC-011 §4.8) and
the `contained_by` relation via the provider's create_relations hook.
Re-runs skip instances already extracted, keyed on (email uuid, attachment
filename) — Gmail's attachment ids are per-fetch handles that change
between fetches. The yield contract replaces the former standalone CLI
pass, whose facts never passed the runtime's scope seam and landed
unstamped.

Account routing (RFC-012 §4.6): the refetch goes through the client of
the account that OWNS the email fact (its metadata.account) — fetching
account B's mail with account A's token 404s and would masquerade as a
deletion. `api_factory(account_subject)` is called lazily per account,
only when an uncovered email forces a refetch; None-keyed legacy facts
(pre-attribution) map to the caller's default account.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from typing import Callable, Iterator, Optional

from system.soil import Soil

from gmail_importer.gmail_api import GmailApiError

from gmail_importer.importer import GmailImporter

# The content-addressed store is shared infrastructure now (the RFC-013
# sessions drain stores browser downloads in the SAME residence):
# promoted to memogarden-utils; re-exported here for existing callers.
from utils.attachments import AttachmentStore, safe_ext as _safe_ext

logger = logging.getLogger(__name__)


class AttachmentExtractor:
    """Plan Gmail attachment downloads for ingested emails.

    Yields runtime-normalized Attachment dicts for declared-but-uncovered
    attachments; the runtime creates the facts (scope-stamped, RFC-011
    §4.8) and the provider's create_relations hook links each to its email
    via `contained_by`. Re-runs are O(new mail) via the (email uuid,
    filename) coverage index — the binaries deduplicate in the
    content-addressed store, so a re-download is cheap and safe.
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
        mirror of the runtime's needs_reauth behavior, kept so one dead
        account cannot fail a multi-account pass.
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

    def _existing_index(self, account_filter: Optional[str] = None) -> dict:
        """(email uuid, attachment filename) -> Attachment fact uuid.

        NB: Gmail attachment IDs are NOT stable — the same message's
        attachment gets a different id on every fetch — so they identify a
        download session, never an extracted instance. Filename is stable.

        Projected in SQL (only the three keys are read): fact-level
        materialization of the whole corpus peaked an hourly no-op run at
        ~1.6 GB. With an account_filter, only that account's attachments
        are indexed (per-account runs cover their own emails).
        """
        if account_filter is not None:
            rows = self.soil._get_connection().execute(
                """SELECT uuid,
                          json_extract(metadata, '$.email_uuid') AS email_uuid,
                          json_extract(data, '$.filename') AS filename
                   FROM item
                   WHERE _type = 'Attachment'
                     AND json_extract(metadata, '$.account') = ?""",
                (account_filter,),
            ).fetchall()
        else:
            rows = self._existing_index_all()
        return {
            (row["email_uuid"], row["filename"]): row["uuid"]
            for row in rows
            if row["email_uuid"] and row["filename"]
        }

    def _existing_index_all(self) -> list:
        return self.soil._get_connection().execute(
            """SELECT uuid,
                      json_extract(metadata, '$.email_uuid') AS email_uuid,
                      json_extract(data, '$.filename') AS filename
               FROM item
               WHERE _type = 'Attachment'"""
        ).fetchall()

    def iter_uncovered(
        self, account_filter: Optional[str] = None, dry_run: bool = False
    ) -> Iterator[dict]:
        """Yield runtime-normalized Attachment dicts for uncovered mail.

        Reads Soil (coverage index + candidate scan); WRITES only the
        binary into the content-addressed store — the fact itself is the
        runtime's to create (providers yield, MemoGarden stores). Steady
        state is a covered-only scan: no API client, no network, O(new).
        """
        # Clients are built lazily per account (see _client_for): a
        # covered-only pass (the steady-state hourly case) skips both the
        # KDF and the network entirely. The Soil context is held for the
        # whole stage (reads only): the API calls are slow but the
        # connection tolerates it — same posture as the retired CLI pass.
        with self.soil:
            existing = self._existing_index(account_filter)
            rows = self._scan_candidates(account_filter)
            self._log(f"{len(rows)} ingested emails declare attachments")

            for row in rows:
                self.stats["emails_scanned"] += 1
                email_uuid = row["uuid"]
                account = row["account"]
                declared = json.loads(row["filenames"]) if row["filenames"] else []
                if declared and all((email_uuid, fn) in existing for fn in declared):
                    self.stats["emails_covered"] += 1
                    continue
                email = self.soil.get_fact(email_uuid)
                if email is None:
                    self.stats["errors"] += 1
                    logger.warning("Email %s vanished mid-scan; skipping attachments", email_uuid)
                    continue
                try:
                    api = self._client_for(account)
                    message = api.get_message(row["gmail_id"])
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
                        logger.info("Email %s deleted at source; skipping attachments", email_uuid)
                        continue
                    self.stats["errors"] += 1
                    logger.warning("Refetch failed for %s: %s", email_uuid, e)
                    continue
                except Exception as e:  # noqa: BLE001 — one bad email must not stop the pass
                    self.stats["errors"] += 1
                    logger.warning("Refetch failed for %s: %s", email_uuid, e)
                    continue
                for item in self._parts_for_email(api, email, message, existing, dry_run):
                    existing[(item["metadata"]["email_uuid"], item["data"]["filename"])] = "new"
                    self.stats["downloaded"] += 1
                    yield item

    def _scan_candidates(self, account_filter: Optional[str] = None) -> list:
        """Projected email scan: uuid, gmail id, owning account, filenames.

        Account-scoped when a filter is given (per-account runs cover only
        their own mail — strictly: NULL-account emails belong to no run,
        and letting each run claim them would refetch cross-mailbox with
        the wrong client; pre-RFC-012 corpora have none — verified
        2026-11-20, zero legacy emails declare attachments).
        """
        sql = """SELECT uuid,
                        json_extract(metadata, '$.gmail_message_id') AS gmail_id,
                        json_extract(metadata, '$.account') AS account,
                        canonical_at,
                        json_extract(data, '$.attachment_filenames') AS filenames
                 FROM item
                 WHERE _type = 'Email'
                   AND json_extract(data, '$.attachment_count') > 0
                   AND json_extract(metadata, '$.gmail_message_id') IS NOT NULL"""
        params: list = []
        if account_filter is not None:
            sql += " AND json_extract(metadata, '$.account') = ?"
            params.append(account_filter)
        rows = self.soil._get_connection().execute(sql, params).fetchall()
        # Order oldest-canonical first for stable, oldest-first sweeps.
        rows.sort(key=lambda r: r["canonical_at"] or "")
        return rows

    def _parts_for_email(
        self, api, email, message: dict, existing: dict, dry_run: bool
    ) -> Iterator[dict]:
        """Walk one refetched message's attachment parts; yield dicts.

        Isolation: a failed refetch already skipped the email; a failed
        part (deleted at source, network, empty payload) skips itself —
        one bad attachment must never fail a run (it will be retried by
        the next coverage scan, which is idempotent).
        """
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
                yield self._build_one(api, email, attachment_id, filename, part)
            except GmailApiError as e:
                if e.status_code == 404:
                    self.stats["attachments_missing"] += 1
                    logger.warning("Attachment %s/%s deleted at source", email.uuid, filename)
                else:
                    self.stats["errors"] += 1
                    logger.warning("Extraction failed for %s/%s: %s", email.uuid, filename, e)
            except Exception as e:  # noqa: BLE001 — skip, retried next scan
                self.stats["errors"] += 1
                logger.warning("Extraction failed for %s/%s: %s", email.uuid, filename, e)

    def _build_one(
        self, api, email, attachment_id: str, filename: str, part: dict
    ) -> dict:
        """Download one part into the store; return the runtime dict.

        NB: the Attachment resource puts `data` at the TOP LEVEL (not under
        `body`) — verified against the live API.
        """
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
        self._log(f"prepared {filename} -> {rel_path} (sha256 {digest[:12]}...)")
        return {
            "type": "Attachment",
            "data": {
                "filename": filename,
                "mime_type": part.get("mimeType", ""),
                "size": len(data),
                "sha256": digest,
                "title": filename,  # searchable like other facts
                "content_path": rel_path,
            },
            "metadata": {
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
            # Arrived with its email — the attachment is older than the run
            "canonical_at": email.canonical_at,
            "realized_at": now,
        }


# Kept for callers/tests that build relation timestamps directly.
_RELATION_EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _days_since_epoch() -> int:
    return (datetime.now(timezone.utc) - _RELATION_EPOCH).days
