"""Gmail Email Importer for MemoGarden Soil.

Fetches messages from the Gmail API and translates them to the same
provider-agnostic Email item shape as the mbox importer (strict RFC 5322
data / provider metadata separation), reusing the mbox email_parser
helpers so both importers stay consistent.

Attachments are captured as metadata only (has_attachments, count,
filenames) — no binaries — matching the decision in
plan/email-implementation-plan.md (Q2).
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

import requests

from system.soil import Soil
from mbox_importer.import_mbox import EmailImporter
from mbox_importer.email_parser import (
    decode_header_value,
    normalize_message_id,
    parse_addresses,
    parse_references,
    strip_angle_brackets,
    validate_email_data,
)

from gmail_importer.gmail_api import (
    HISTORY_PAGES,
    GmailApiClient,
    GmailApiError,
    find_oldest_message_ids,
    page_budget_for,
    walk_history,
)
from gmail_importer.search_cache import SearchCache

logger = logging.getLogger(__name__)

# Hourly runs between 6-hourly cache refreshes that one search must cover.
_SEARCH_HEADROOM_RUNS = 5

# Per-run page budget for the reverse-chronological backfill walk (RFC-012
# §4.10): 40 pages ≈ 20k ids, the platform's enumeration horizon. Hitting
# it defers the remainder to the next run — it must never fail the sync.
_BACKFILL_MAX_PAGES = 40

_MAX_METADATA_HTML_BYTES = 10000  # same truncation as mbox importer


class GmailImporter(EmailImporter):
    """Import emails from the Gmail API.

    Uses HACM for OAuth credentials: a refresh token stored under
    `google:{account_id}` and client credentials under `google:oauth-client`.
    """

    def __init__(
        self,
        soil: Soil,
        account_id: str,
        hacm_path: str,
        passphrase: Optional[str] = None,
        batch_size: int = 50,
        search_cache_path: Optional[str] = None,
        sync_state_path: Optional[str] = None,
        excluded_ids_path: Optional[str] = None,
    ):
        # Slim-scan memo (2026-11-20): the base class builds email_index and
        # dedup_index via TWO separate json_extract scans of the item table.
        # At the current corpus (39.5k attributed emails over a 1.86 GB
        # table) that construction transiently allocates ~2 GB — past the
        # service's MemoryHigh=1G cap, triggering the swap-storm → gunicorn
        # worker-timeout massacre (every gmail run orphaned 2026-11-20
        # 16:28–17:13). ONE combined pass with a small page cache does both
        # indexes at ~56 MB / 1.5 s. Must precede super().__init__ (which
        # calls the builders).
        self._slim_email_scan: Optional[list] = None
        super().__init__(soil, batch_size)
        self.account_id = account_id
        self.hacm_path = hacm_path
        self.passphrase = passphrase
        # Progressive mode only: path of the oldest-window search cache.
        # Hourly runs serve slices from the cache and only rerun the full
        # search every 6h (or when the cursor needs ids beyond it).
        self.search_cache_path = search_cache_path
        # Incremental mode only: path of the historyId sync state
        # (plan/gmail-incremental-sync-design.md).
        self.sync_state_path = sync_state_path
        # All modes: gmail message ids that are gone at the source (deleted,
        # purged) — persisted so they stop being selected for fetch. They are
        # NOT in the done set: they were never ingested, and never will be.
        self.excluded_ids_path = excluded_ids_path
        self._limit: Optional[int] = None

    # ------------------------------------------------------------------
    # OAuth plumbing
    # ------------------------------------------------------------------

    @staticmethod
    def _load_oauth_client():
        """Load GmailOAuthClient from the gmail-oauth source directory.

        The gmail-oauth directory ships as `gmail-oauth/` (dash, not a valid
        package name), so it is loaded by path rather than installed.
        """
        import importlib.util
        import sys
        from pathlib import Path

        try:
            import gmail_oauth  # type: ignore

            return gmail_oauth.GmailOAuthClient
        except ImportError:
            pass

        oauth_dir = Path(__file__).resolve().parents[3] / "gmail-oauth"
        init_py = oauth_dir / "__init__.py"
        if not init_py.exists():
            raise ImportError(
                f"gmail-oauth source not found at {oauth_dir}; expected it "
                "next to providers/ in the repository root"
            )
        spec = importlib.util.spec_from_file_location(
            "gmail_oauth", init_py, submodule_search_locations=[str(oauth_dir)]
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["gmail_oauth"] = module
        spec.loader.exec_module(module)
        return module.GmailOAuthClient

    def _slim_scan_rows(self) -> list:
        """One projected pass over Email facts for both dedup indexes.

        The slim-SQL override flagged pending since the Gmail ingestion
        implementation (trigger: "batch-1000 runs creep" — reached): the
        base class's two full-table json_extract scans transiently
        allocated ~2 GB per importer construction; this single pass with
        PRAGMA cache_size pinned to 2 MB measures ~56 MB / 1.5 s over the
        same corpus. Runs once per importer (both indexes share it).
        """
        if self._slim_email_scan is None:
            conn = self.soil._get_connection()
            conn.execute("PRAGMA cache_size = -2000")  # 2 MB page cache
            self._slim_email_scan = conn.execute(
                """SELECT uuid,
                          json_extract(data, '$.rfc_message_id') AS mid,
                          json_extract(metadata, '$.account') AS account
                   FROM item
                   WHERE _type = 'Email'"""
            ).fetchall()
        return self._slim_email_scan

    def _build_index(self) -> dict[str, str]:
        """rfc_message_id -> uuid, from the shared slim scan."""
        return {
            row["mid"]: row["uuid"]
            for row in self._slim_scan_rows()
            if row["mid"]
        }

    def _build_dedup_index(self) -> dict[tuple[str | None, str], str]:
        """(account, rfc_message_id) -> uuid, from the shared slim scan."""
        return {
            (row["account"], row["mid"]): row["uuid"]
            for row in self._slim_scan_rows()
            if row["mid"]
        }

    def _build_api_client(self) -> GmailApiClient:
        """Refresh the access token via HACM-stored credentials.

        Credential-shaped failures (missing refresh token, Google rejecting
        the grant) raise GmailApiError with auth_failure=True — the runtime
        parks such an account into needs_reauth (RFC-012 §4.2) instead of
        retrying it hourly. Transport/infrastructure failures keep
        auth_failure=False and retry on the next heartbeat.
        """
        oauth_cls = self._load_oauth_client()
        oauth = oauth_cls(self.hacm_path, passphrase=self.passphrase)
        try:
            tokens = oauth.refresh_access_token(self.account_id)
        except ValueError as e:
            # ValueError here is the oauth client's own contract: a missing
            # refresh token or a Google-declared error (invalid_grant etc.)
            raise GmailApiError(
                f"credential refresh failed for {self.account_id}: {e}",
                auth_failure=True,
            ) from None
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            raise GmailApiError(
                f"credential refresh failed for {self.account_id}: {e}",
                status_code=status,
                auth_failure=status is not None and status < 500,
            ) from None
        return GmailApiClient(tokens["access_token"])

    # ------------------------------------------------------------------
    # Fetch + translate
    # ------------------------------------------------------------------

    def fetch_emails(
        self, since: Optional[datetime] = None, verbose: bool = True
    ) -> Iterator[dict]:
        """Fetch emails from Gmail (oldest-N, or the progressive next batch).

        Args:
            since: Unused (kept for the EmailImporter interface; date-range
                sync lands with the background scheduler work).
            verbose: Print search progress.

        Yields:
            Email dicts in the same shape as mbox_message_to_email_item.
        """
        api = self._build_api_client()
        # The base pipeline calls fetch_emails() with no args, so --quiet
        # flows through instance state set by import_oldest()/import_next().
        verbose = getattr(self, "_verbose", verbose)

        self.stats.setdefault("deleted", 0)
        if getattr(self, "_progressive", False):
            done = getattr(self, "_ingested_ids", set())
            excluded = getattr(self, "_excluded_ids", set())
            batch_ids, exhausted = self._next_batch_ids(api, done | excluded, self._limit or 100)
            if exhausted:
                self._log_progress(verbose, len(done))
                return
        elif getattr(self, "_incremental", False):
            done = getattr(self, "_ingested_ids", set())
            excluded = getattr(self, "_excluded_ids", set())
            batch_ids = self._incremental_batch_ids(api, done | excluded, self._limit or 500)
            if not batch_ids:
                if verbose:
                    print("[incremental] no new mail since stored historyId")
                return
        else:
            ids = find_oldest_message_ids(
                api, target=self._limit or 10, verbose=verbose
            )
            batch_ids = list(reversed(ids))[: self._limit]

        realized_at = datetime.now(timezone.utc).isoformat()
        label_filter = getattr(self, "_label_filter", False)
        excluded = getattr(self, "_excluded_ids", None)
        for message_id in batch_ids:
            try:
                message = api.get_message(message_id)
            except GmailApiError as e:
                if e.status_code == 404:
                    # 404 on fetch = the message was deleted at the source
                    # after being listed (or user-purged). Exclude it
                    # permanently so it stops occupying batch slots, and do
                    # NOT count it as an error — this is mail lifecycle.
                    self._exclude_ids([message_id], "deleted_at_source")
                    if excluded is not None:
                        excluded.add(message_id)
                    self.stats["deleted"] += 1
                    logger.info(
                        "Message %s deleted at source — excluded from future batches",
                        message_id,
                    )
                    continue
                self.stats["errors"] += 1
                logger.warning("Failed to fetch message %s: %s", message_id, e)
                continue
            if label_filter and (
                "TRASH" in (message.get("labelIds") or [])
                or "SPAM" in (message.get("labelIds") or [])
            ):
                # Ingestion policy: all mail except what is currently in
                # TRASH or SPAM — evaluated at fetch time (history records
                # may be stale, e.g. spammed-then-rescued within a window).
                self.stats["skipped"] += 1
                continue
            email_dict = self.translate_gmail_to_email(
                message, realized_at=realized_at
            )
            if email_dict:
                yield email_dict
            else:
                self.stats["skipped"] += 1

    def _log_progress(self, verbose: bool, done_count: int) -> None:
        if verbose:
            print(
                f"[gmail search] {done_count} ingested; enumerated window "
                "fully ingested — nothing further to fetch"
            )

    def _next_batch_ids(
        self, api: GmailApiClient, done: set[str], limit: int
    ) -> tuple[list[str], bool]:
        """`done` here is the combined skip set: ingested ids plus ids
        excluded as deleted-at-source."""
        """Ids of up to `limit` oldest messages NOT in the done set.

        The done set is the gmail_message_ids of every Email fact already in
        Soil (read at run start). It survives skips: a message that failed
        validation is retried in later batches and self-heals once
        validation accepts it — the batch is the first `limit` window ids
        not in the set, so skipped messages occupy a slot but never block
        progress.

        Serves from the search cache when fresh and long enough (see
        search_cache.py); otherwise runs the full search and restores it.

        Returns (batch_ids, exhausted) — exhausted=True when every message
        in the enumerated window is already ingested.
        """
        cache = (
            SearchCache(self.search_cache_path) if self.search_cache_path else None
        )
        min_length = len(done) + limit
        if cache is not None:
            cached = cache.load_valid(min_length=min_length)
            if cached is not None:
                remaining = [i for i in cached if i not in done]
                return remaining[:limit], not remaining

        # Overprovision the searched window by ~5 runs so the cache keeps
        # serving hourly batches until the next 6-hourly refresh — searching
        # exactly done+limit would make every subsequent run miss by one
        # batch. Extra enumeration cost is at most a page or two.
        target = min_length + limit * _SEARCH_HEADROOM_RUNS
        ids = find_oldest_message_ids(
            api,
            target=target,
            verbose=getattr(self, "_log_verbose", False),
            page_budget=page_budget_for(len(done), target - len(done)),
        )
        oldest_first = list(reversed(ids))
        if cache is not None:
            cache.store(oldest_first)
        remaining = [i for i in oldest_first if i not in done]
        return remaining[:limit], not remaining

    def _newest_batch_ids(
        self, api: GmailApiClient, done: set[str], limit: int,
        before: Optional[str] = None, max_pages: int = _BACKFILL_MAX_PAGES,
    ) -> tuple[list[str], bool, Optional[str]]:
        """Ids of up to `limit` NEWEST messages not in done, newest-first.

        RFC-012 §4.10 ingestion-order policy: history backfill walks the
        mailbox reverse-chronologically — the most recent mail first, so an
        account is useful from its first run — until the oldest email is
        ingested. `messages.list` is natively newest-first, so this is a
        plain paged walk from the head: no full enumeration, no search
        cache (the CLI's oldest-first path owns that file; the two
        orderings must never share it).

        Pages are walked from the start each call, skipping done ids, until
        the batch fills. Depth grows as the backfill progresses but stays
        bounded by the per-run page budget (40 pages ~ 20k ids); hitting
        the budget DEFERS the remainder to the next run rather than failing
        it. For mailboxes deeper than the horizon, the provider tightens a
        `before:` date cursor (boundary_id) so the walk continues in the
        not-yet-covered, older region on subsequent runs. `before`
        (YYYY/MM/DD) applies that cursor as a Gmail date query when set.

        `done` is the combined skip set (ingested + excluded + anything
        this run already yielded).

        Returns (batch_ids, exhausted, boundary_id): exhausted=True only
        when the full (bounded) listing was walked — the oldest email is
        inside this batch or already gone, and after it lands the account's
        backfill is complete. boundary_id is set ONLY when the page budget
        was hit: the oldest-position id seen in the walk, for the provider
        to tighten the date cursor past the covered region.
        """
        batch: list[str] = []
        token: Optional[str] = None
        boundary_id: Optional[str] = None
        last_seen: Optional[str] = None
        query = f"before:{before}" if before else None
        for _ in range(max_pages):
            page = api.list_messages(query, token)
            for entry in page.get("messages", []) or []:
                message_id = entry["id"]
                last_seen = message_id
                if message_id in done:
                    continue
                batch.append(message_id)
                if len(batch) >= limit:
                    return batch, False, None
            boundary_id = last_seen
            token = page.get("nextPageToken") or None
            if not token:
                boundary_id = None
                return batch, True, None  # walked the whole (bounded) listing
        return batch, False, boundary_id  # budget hit mid-mailbox: defer

    # ------------------------------------------------------------------
    # historyId incremental sync
    # ------------------------------------------------------------------

    def _load_excluded_ids(self) -> set[str]:
        """gmail message ids known gone at the source (deleted/purged)."""
        if not self.excluded_ids_path:
            return set()
        try:
            doc = json.loads(Path(self.excluded_ids_path).read_text())
        except (OSError, ValueError):
            return set()
        ids = doc.get("ids")
        return set(ids) if isinstance(ids, list) else set()

    def _exclude_ids(self, ids: list[str], reason: str) -> None:
        if not self.excluded_ids_path or not ids:
            return
        try:
            doc = json.loads(Path(self.excluded_ids_path).read_text())
        except (OSError, ValueError):
            doc = {"ids": []}
        known = {i for i in doc.get("ids", []) if isinstance(i, str)}
        added = [i for i in ids if i not in known]
        if not added:
            return
        doc["ids"] = sorted(known | set(added))
        reasons = doc.setdefault("reasons", {})
        reasons[reason] = reasons.get(reason, 0) + len(added)
        doc["updated_at"] = datetime.now(timezone.utc).isoformat()
        state_path = Path(self.excluded_ids_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path.with_suffix(state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(doc))
        tmp.rename(state_path)

    def _load_sync_state(self) -> dict:
        if not self.sync_state_path:
            return {}
        try:
            doc = json.loads(Path(self.sync_state_path).read_text())
        except (OSError, ValueError):
            return {}
        if doc.get("account") != self.account_id:
            return {}  # different account — reseed
        return doc

    def _store_sync_state(
        self,
        history_id: str,
        backfill: Optional[str] = None,
        backfill_before: Optional[str] = None,
    ) -> None:
        """Persist the per-account sync state.

        `backfill`: RFC-012 §4.10 — 'complete' once the reverse-chronological
        history walk has ingested the mailbox down to its oldest email.
        None preserves whatever value the account's current doc carries
        (read through the account-guarded loader; 'running' is the implicit
        default for a fresh account).

        `backfill_before`: the §4.10 horizon-walk date cursor (YYYY/MM/DD)
        when the mailbox is deeper than one run's page budget. None
        preserves the current value; empty string clears it.
        """
        state_path = Path(self.sync_state_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        existing = self._load_sync_state()
        if backfill_before is None:
            backfill_before = existing.get("backfill_before") or ""
        doc = {
            "account": self.account_id,
            "history_id": str(history_id),
            "backfill": backfill or existing.get("backfill") or "running",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if backfill_before:
            doc["backfill_before"] = backfill_before
        tmp = state_path.with_suffix(state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(doc))
        tmp.rename(state_path)
        self._log_verbose_log("sync state advanced to historyId %s" % history_id)

    def _log_verbose_log(self, msg: str) -> None:
        if getattr(self, "_log_verbose", False):
            print(f"[incremental] {msg}")

    def _incremental_batch_ids(
        self, api: GmailApiClient, done: set[str], limit: int
    ) -> list[str]:
        """`done` here is the combined skip set: ingested ids plus ids
        excluded as deleted-at-source."""
        """Gmail message ids to ingest this run, per the stored historyId.

        Candidates = messagesAdded + labelsRemoved records (rescue events).
        Returns ids oldest-change-first, filtered through the done set and
        capped at `limit`; when the cap truncates candidates, the returned
        history id is None so the sync state is NOT advanced (the remainder
        is re-delivered next run and absorbed by dedup). A backlog larger
        than one run's page budget is not an error: the id resumes at the
        last record read, so the backlog drains across runs.
        """
        state = self._load_sync_state()
        profile = api.get_profile()
        current = str(profile.get("historyId") or "")
        stored = state.get("history_id")

        if not stored:
            self._pending_history_id = current
            self._log_verbose_log(
                f"seeded history sync at {current} — new mail flows from the next run"
            )
            return []

        candidates: list[str] = []
        try:
            records, history_complete, history_resume = walk_history(api, stored)
        except GmailApiError as e:
            if e.status_code == 404:
                # History expired: reseed at the current id. The gap mail is
                # still inside the progressive backfill window (it covers
                # the whole mailbox up to 20k), so nothing is lost.
                self._log_verbose_log(
                    f"history since {stored} expired — reseeded at {current}"
                )
                self._pending_history_id = current
                return []
            raise
        for record in records:
            for entry in record.get("messagesAdded") or []:
                candidates.append(entry["message"]["id"])
            for entry in record.get("labelsRemoved") or []:
                candidates.append(entry["message"]["id"])
            for entry in record.get("messagesDeleted") or []:
                # Gmail-native deletion signal: exclude before fetch
                self._exclude_ids([entry["message"]["id"]], "messagesDeleted")
                done.add(entry["message"]["id"])
        if not history_complete and history_resume is None:
            # Pages remained but none carried a record id: nothing to resume
            # from. Fail loudly rather than skip unread history.
            raise GmailApiError(
                f"history page budget exceeded (>{HISTORY_PAGES} pages) "
                "with no record id to resume from"
            )

        fully_processed = len(candidates) <= limit
        remaining = [i for i in candidates if i not in done]
        if fully_processed:
            # A complete walk jumps to the mailbox's current id; one that hit
            # its page budget resumes at the last record read.
            self._pending_history_id = current if history_complete else history_resume
        else:
            self._pending_history_id = None  # partial: re-diff next run
        self._log_verbose_log(
            f"{len(candidates)} history candidates, {len(remaining)} new"
        )
        return remaining[:limit]

    def import_oldest(self, limit: int = 10, verbose: bool = True) -> dict:
        """Import the `limit` oldest messages (dedup + threading via base class).

        Re-running with the same limit after a successful import is a no-op
        (the same oldest messages deduplicate); growth requires raising limit.
        """
        self._limit = limit
        self._verbose = verbose
        self._log_verbose = verbose
        try:
            return self.import_all(verbose=verbose)
        finally:
            self._limit = None

    def import_next(self, limit: int = 100, verbose: bool = True) -> dict:
        """Progressive ingestion: import up to `limit` of the oldest messages
        that are not yet in Soil.

        Progress is tracked by the set of ingested gmail_message_ids (read
        from Soil at run start): each run selects the oldest not-yet-ingested
        window ids, so skipped messages are retried in later runs (and
        self-heal if validation changes) and ingested ones are never
        re-fetched. Returns the import stats (counters are cumulative per
        importer instance).
        """
        self._limit = limit
        self._verbose = verbose
        self._log_verbose = verbose
        self._progressive = True
        self._excluded_ids = self._load_excluded_ids()
        self.stats.setdefault("deleted", 0)
        try:
            with self.soil:
                self._ingested_ids = self._ingested_gmail_ids()
            return self.import_all(verbose=verbose)
        finally:
            self._progressive = False
            self._ingested_ids = None
            self._excluded_ids = None
            self._limit = None

    def import_incremental(self, limit: int = 500, verbose: bool = True) -> dict:
        """historyId-based incremental sync (plan/gmail-incremental-sync-
        design.md): fetch mail NEW since the stored historyId.

        First run seeds: stores the profile's current historyId and imports
        nothing — mail arriving after seeding appears in later diffs. The
        sync state is advanced only after a successful import, and only
        when every candidate fit the batch (a burst larger than `limit`
        leaves the state untouched, so the remainder is re-delivered next
        run and absorbed by dedup). Candidates come from messagesAdded AND
        labelsRemoved history records (rescue events); the ingestion policy
        (all mail except what is currently in TRASH or SPAM) is evaluated
        at fetch time. On history expiry (404) the pass reseeds at the
        current historyId and logs — the gap is covered by the progressive
        backfill window.
        """
        self._limit = limit
        self._verbose = verbose
        self._log_verbose = verbose
        self._incremental = True
        self._label_filter = True
        self._excluded_ids = self._load_excluded_ids()
        try:
            with self.soil:
                self._ingested_ids = self._ingested_gmail_ids()
            stats = self.import_all(verbose=verbose)
            pending = getattr(self, "_pending_history_id", None)
            if pending is not None and self.sync_state_path:
                self._store_sync_state(pending)
            return stats
        finally:
            self._incremental = False
            self._label_filter = False
            self._ingested_ids = None
            self._excluded_ids = None
            self._pending_history_id = None
            self._limit = None

    def _ingested_gmail_ids(self) -> set[str]:
        """This account's ingested gmail_message_ids, projected in SQL.

        RFC-012 §4.6: gmail message ids are per-mailbox namespaces — the
        scan filters on metadata.account so one account's done-set never
        absorbs another's ids. An empty filtered scan with NO attributed
        gmail fact anywhere in the corpus falls back to the unfiltered
        legacy scan (pre-backfill defense, RFC-012 §8 M5); once any
        attributed fact exists, the filter is strict.
        """
        rows = self.soil._get_connection().execute(
            """SELECT json_extract(metadata, '$.gmail_message_id') AS mid
               FROM item
               WHERE _type = 'Email'
                 AND json_extract(metadata, '$.account') = ?""",
            (self.account_id,),
        ).fetchall()
        mids = {row["mid"] for row in rows if row["mid"]}
        if mids:
            return mids
        row = self.soil._get_connection().execute(
            """SELECT COUNT(*) AS n FROM item
               WHERE _type = 'Email'
                 AND json_extract(metadata, '$.gmail_message_id') IS NOT NULL
                 AND json_extract(metadata, '$.account') IS NOT NULL"""
        ).fetchone()
        if row["n"]:
            return set()
        legacy = self.soil._get_connection().execute(
            """SELECT json_extract(metadata, '$.gmail_message_id') AS mid
               FROM item
               WHERE _type = 'Email'"""
        ).fetchall()
        return {row_["mid"] for row_ in legacy if row_["mid"]}

    def translate_gmail_to_email(
        self, gmail_msg: dict, realized_at: Optional[str] = None
    ) -> Optional[dict]:
        """Translate a Gmail API message resource to an Email item dict.

        Mirrors mbox_message_to_email_item: RFC 5322 fields into data,
        Gmail-specific fields into metadata, attachments as metadata only.
        Returns None for messages that cannot be deduplicated (no
        Message-ID), matching the mbox importer.
        """
        if realized_at is None:
            realized_at = datetime.now(timezone.utc).isoformat()

        headers: dict[str, str] = {}
        for header in gmail_msg.get("payload", {}).get("headers", []):
            name = header["name"].lower()
            if name not in headers:
                headers[name] = header["value"]

        raw_message_id = headers.get("message-id", "")
        if not raw_message_id:
            return None  # cannot deduplicate; same rule as mbox path
        normalized_message_id = normalize_message_id(raw_message_id)

        sent_at = self._internal_date_to_iso(gmail_msg.get("internalDate"))
        if not sent_at:
            from mbox_importer.email_parser import parse_date

            sent_at = parse_date(headers.get("date", "")) or realized_at

        plain_text, html_body, attachments = self._extract_bodies(
            gmail_msg.get("payload", {})
        )
        attachment_filenames = [a["filename"] for a in attachments]

        if plain_text:
            body = plain_text
            content_type = "text/plain"
        elif html_body:
            body = html_body
            content_type = "text/html"
        else:
            body = ""
            content_type = "text/plain"

        references = parse_references(headers["references"]) if headers.get("references") else None
        in_reply_to = (
            strip_angle_brackets(headers["in-reply-to"])
            if headers.get("in-reply-to")
            else None
        )

        metadata = {
            "provider": "google",
            "source": "gmail_api",
            "gmail_message_id": gmail_msg.get("id"),
            # RFC-012 §4.7: mailbox attribution at creation — which account's
            # credentials produced this fact. Metadata sits outside the fact
            # integrity hash (data only), so stamping never disturbs hashes.
            "account": self.account_id,
        }
        if gmail_msg.get("threadId"):
            metadata["gmail_thread_id"] = gmail_msg["threadId"]
        if gmail_msg.get("labelIds"):
            metadata["gmail_labels"] = gmail_msg["labelIds"]
        if attachments:
            # Provenance for later attachment extraction. NB: Gmail
            # attachment ids are session-scoped handles that change between
            # fetches — extraction refetches the message for current ids.
            metadata["gmail_attachments"] = attachments
        if html_body and content_type == "text/plain":
            # Store HTML in metadata when plain text is the primary body
            metadata["html_body"] = html_body[:_MAX_METADATA_HTML_BYTES]

        data = {
            "rfc_message_id": normalized_message_id,
            "from_address": decode_header_value(headers.get("from", "")),
            "to_addresses": parse_addresses(headers.get("to", "")),
            "cc_addresses": parse_addresses(headers["cc"]) if headers.get("cc") else None,
            "bcc_addresses": parse_addresses(headers["bcc"]) if headers.get("bcc") else None,
            "sent_at": sent_at,
            "received_at": None,
            "references": references,
            "in_reply_to": in_reply_to,
            "title": decode_header_value(headers.get("subject", "")),
            "description": body,
            "content_type": content_type,
            "has_attachments": bool(attachment_filenames),
            "attachment_count": len(attachment_filenames),
            "attachment_filenames": attachment_filenames,
        }

        try:
            validate_email_data(data)
        except ValueError as e:
            logger.warning("Skipping invalid Gmail message %s: %s", gmail_msg.get("id"), e)
            return None

        return {
            "_type": "Email",
            "realized_at": realized_at,
            "canonical_at": sent_at,
            "fidelity": "full",
            "data": data,
            "metadata": metadata,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _internal_date_to_iso(internal_date: Optional[str]) -> Optional[str]:
        """Gmail internalDate (ms since epoch, string) -> ISO 8601 UTC."""
        if not internal_date:
            return None
        try:
            seconds = int(internal_date) / 1000
        except (TypeError, ValueError):
            return None
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()

    @staticmethod
    def _walk_parts(payload: dict) -> Iterator[dict]:
        """Depth-first walk of a MIME payload tree."""
        yield payload
        for part in payload.get("parts", []) or []:
            yield from GmailImporter._walk_parts(part)

    @staticmethod
    def _part_headers(part: dict) -> dict[str, str]:
        return {
            h["name"].lower(): h["value"] for h in part.get("headers", [])
        }

    def _extract_bodies(
        self, payload: dict
    ) -> tuple[Optional[str], Optional[str], list[dict]]:
        """Extract (plain_text, html_body, attachments).

        Attachment = part with a filename AND Content-Disposition: attachment
        (same rule as the mbox importer's parser; note the Gmail path counts
        only NAMED attachments, and inline files are never attachments).
        Each attachment is a record {filename, attachment_id, mime_type, size}
        — attachment_id enables direct binary fetch later. Bodies are
        base64url decoded using the part's declared charset — the oldest
        mail is the most likely to use legacy charsets, and Soil facts are
        immutable, so a wrong decode would persist forever.
        """
        plain_text: Optional[str] = None
        html_body: Optional[str] = None
        attachments: list[dict] = []

        for part in self._walk_parts(payload):
            part_headers = self._part_headers(part)
            disposition = part_headers.get("content-disposition", "")
            filename = part.get("filename") or ""

            if filename and "attachment" in disposition.lower():
                attachments.append(
                    {
                        "filename": decode_header_value(filename),
                        "attachment_id": part.get("body", {}).get("attachmentId"),
                        "mime_type": part.get("mimeType", ""),
                        "size": part.get("body", {}).get("size"),
                    }
                )
                continue
            if filename:
                # Inline files (Content-Disposition: inline) are not
                # attachments per the mbox importer's definition.
                continue

            mime_type = part.get("mimeType", "")
            if mime_type not in ("text/plain", "text/html"):
                continue
            body_data = part.get("body", {}).get("data")
            if not body_data:
                continue
            text = self._decode_body(body_data, part_headers.get("content-type", ""))
            if text is None:
                continue
            if mime_type == "text/plain" and plain_text is None:
                plain_text = text
            elif mime_type == "text/html" and html_body is None:
                html_body = text

        return plain_text, html_body, attachments

    @staticmethod
    def _decode_body(body_data: str, content_type_header: str) -> Optional[str]:
        """Decode a base64url body using its declared charset, utf-8 fallback."""
        import re as _re

        try:
            raw = base64.urlsafe_b64decode(body_data + "=" * (-len(body_data) % 4))
        except Exception:
            return None
        charset_match = _re.search(
            r'charset=["\']?([A-Za-z0-9_\-.]+)', content_type_header
        )
        charset = charset_match.group(1) if charset_match else "utf-8"
        try:
            return raw.decode(charset, errors="replace")
        except (LookupError, UnicodeDecodeError):
            return raw.decode("utf-8", errors="replace")
