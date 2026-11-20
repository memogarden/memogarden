"""GmailProvider — the gmail-importer's incremental diff as a registered
Provider Protocol implementation (PRD Provider Plugin Interface; RFC-005
registry entry `gmail`).

The provider's cursor is the stored historyId (gmail-sync-state.json) —
provider-managed, so the runtime's `since` timestamp is accepted but
ignored (a timestamp cannot address Gmail's change log). sync() yields
dicts in the runtime's normalized shape ({type, data, metadata,
canonical_at, realized_at}); the runtime validates and inserts (PRD split:
providers yield, MemoGarden stores).

Ingestion policy (as everywhere in this importer): all mail except what is
currently in TRASH or SPAM, evaluated at fetch time. Deleted-at-source ids
are excluded via the shared exclusion file.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

from gmail_importer.gmail_api import GmailApiClient, GmailApiError
from gmail_importer.importer import GmailImporter

logger = logging.getLogger(__name__)

_RELATION_EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc)

_DEFAULT_HACM_PATH = "~/.local/share/memogarden/hacm/credentials.enc"
_DEFAULT_PASSPHRASE_FILE = "~/.local/share/memogarden/hacm/passphrase"
_DEFAULT_BATCH_LIMIT = 500
_DEFAULT_ATTACHMENT_STORE = "~/.local/share/memogarden/attachments"


def _defaults_for(subject: Optional[str]) -> dict:
    """Default config, state paths per subject (RFC-012 §4.6).

    With an account context, state files are subject-qualified so accounts
    never share cursors; without one (legacy single-account registration)
    the historical flat filenames are kept. Explicit config values always
    win — the migrated legacy account row carries its historical absolute
    paths in its own config.
    """
    defaults = {
        "hacm_path": _DEFAULT_HACM_PATH,
        "passphrase_file": _DEFAULT_PASSPHRASE_FILE,
        "batch_limit": _DEFAULT_BATCH_LIMIT,
        # The content-addressed attachment store is a data-dir fixture, not
        # per-account state (RFC-013 sessions share the same residence).
        "attachment_store_root": _DEFAULT_ATTACHMENT_STORE,
    }
    if subject:
        defaults["sync_state_path"] = (
            f"~/.local/share/memogarden/gmail-sync-state--{subject}.json"
        )
        defaults["excluded_ids_path"] = (
            f"~/.local/share/memogarden/gmail-excluded-ids--{subject}.json"
        )
    else:
        defaults["sync_state_path"] = "~/.local/share/memogarden/gmail-sync-state.json"
        defaults["excluded_ids_path"] = (
            "~/.local/share/memogarden/gmail-excluded-ids.json"
        )
    return defaults


class GmailProvider:
    """Provider Protocol implementation over the gmail-importer machinery.

    Account-backed (RFC-012 §4.5): the runtime constructs one instance per
    (sync run, account), passing the account context (id, platform, subject,
    label, credential_ref, config — never secrets). `config` arrives
    pre-merged {provider config, account config} from the runtime.
    """

    PLATFORMS = ["google"]

    def __init__(self, config: dict, account: Optional[dict] = None):
        if account is not None:
            subject = account["subject"]
        else:
            # Legacy shape: the account was a config value. Still supported
            # for direct/dev construction; the live registry uses accounts.
            subject = (config or {}).get("account")
        if not subject:
            raise ValueError(
                "GmailProvider needs an account context (runtime) or an "
                "'account' config value (legacy)"
            )
        self.account = subject
        merged = {
            **_defaults_for(subject if account is not None else None),
            **(config or {}),
        }
        self.batch_limit = int(merged.get("batch_limit", _DEFAULT_BATCH_LIMIT))
        expand = lambda p: str(Path(p).expanduser())
        self.attachment_store_root = expand(merged["attachment_store_root"])

        # Soil: explicit path override, else the runtime's default resolution
        # (verb-based — identical to whatever environment we execute in).
        if merged.get("soil_path"):
            from system.soil import Soil

            soil = Soil(expand(merged["soil_path"]))
        else:
            from system.soil import get_soil

            soil = get_soil()

        self._soil = soil
        with soil:
            # Context covers construction AND schema init (idempotent):
            # GmailImporter.__init__ builds its dedup index immediately.
            soil.init_schema()
            self.importer = GmailImporter(
                soil=soil,
                account_id=self.account,
                hacm_path=expand(merged["hacm_path"]),
                passphrase=self._passphrase(merged),
                sync_state_path=expand(merged["sync_state_path"]),
                excluded_ids_path=expand(merged["excluded_ids_path"]),
            )

    def _log(self, msg: str) -> None:
        if getattr(self, "_log_verbose", True):
            print(f"[gmail provider] {msg}")

    @staticmethod
    def _passphrase(config: dict) -> Optional[str]:
        pf = Path(
            config.get("passphrase_file", _DEFAULT_PASSPHRASE_FILE)
        ).expanduser()
        return pf.read_text().strip() if pf.exists() else None

    # ------------------------------------------------------------------
    # RFC-012 §6.1: server-side credential verification hook
    # ------------------------------------------------------------------

    @staticmethod
    def verify_account(account: dict, config: dict) -> dict:
        """Probe credential health: refresh + profile + subject cross-check.

        Called by the API's POST /providers/{name}/accounts/{id}/verify via
        the runtime. `config` arrives pre-merged {provider, account} like a
        run's config. A passing probe also re-attests the subject (the same
        invariant as link-time verification, re-checked on demand).
        """
        subject = account["subject"]
        merged = {**_defaults_for(subject), **(config or {})}
        expand = lambda p: str(Path(p).expanduser())
        try:
            client = build_client_for_account(
                subject,
                expand(merged["hacm_path"]),
                GmailProvider._passphrase(merged),
            )
            profile = client.get_profile()
        except GmailApiError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001 — any probe failure is a failed probe
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        email = (profile.get("emailAddress") or "").lower()
        if email != subject.lower():
            return {
                "ok": False,
                "error": (
                    f"credential authenticated as {email or '<unknown>'}, "
                    f"expected {subject}"
                ),
            }
        return {"ok": True, "subject": email}

    # ------------------------------------------------------------------
    # Provider Protocol
    # ------------------------------------------------------------------

    def sync(self, since: Optional[str] = None) -> Iterator[dict]:
        """Yield Email facts for mail NEW since the stored historyId, plus
        the account's reverse-chronological history backfill (RFC-012 §4.10
        ingestion-order policy).

        Two stages per run:
        1. **historyId incremental** — mail new since the stored cursor
           (bounded latency for incoming mail), exactly as before.
        2. **history backfill** — until the account's backfill is complete,
           a newest-first batch (most recent un-ingested mail first), so a
           fresh account starts with its most recent, most useful mail and
           walks backward toward its oldest email across heartbeats.

        Args:
            since: Ignored — the historyId in the sync state is the cursor
                (a timestamp cannot address Gmail's change log).

        Cursor advancement: the sync state (historyId AND backfill status)
        advances when the runtime consumes the full batch (this generator
        runs to completion) and nothing was truncated; truncation leaves
        both untouched (the remainder re-delivers next sync; the done-set
        absorbs what already landed), and truncating incremental mail
        defers the backfill stage to the next run.
        """
        api: GmailApiClient = self.importer._build_api_client()
        done = self._ingested_gmail_ids()
        excluded = self.importer._load_excluded_ids()
        skip = done | excluded

        state = self.importer._load_sync_state()
        profile = api.get_profile()
        current = str(profile.get("historyId") or "")
        stored = state.get("history_id")
        backfill_done = state.get("backfill") == "complete"

        # RFC-012 INV-3, re-checked at run time: the credential must belong
        # to the configured subject. A mismatch means the HACM entry under
        # this account's name authorizes a DIFFERENT mailbox — syncing here
        # would poison the account's cursor and done-set.
        profile_email = (profile.get("emailAddress") or "").lower()
        if profile_email and profile_email != self.account.lower():
            raise GmailApiError(
                f"mailbox mismatch: credential authenticated as {profile_email}, "
                f"account configured as {self.account}",
                status_code=403,
                auth_failure=True,
            )

        candidates: list[str] = []
        remaining: list[str] = []
        if not stored:
            self._log(f"seeding history sync at {current} — new mail flows from the next sync")
        else:
            token: Optional[str] = None
            resp: Optional[dict] = None
            try:
                for _ in range(20):
                    resp = api.history_list(stored, token)
                    for record in resp.get("history") or []:
                        for entry in record.get("messagesAdded") or []:
                            candidates.append(entry["message"]["id"])
                        for entry in record.get("labelsRemoved") or []:
                            candidates.append(entry["message"]["id"])
                    token = resp.get("nextPageToken")
                    if not token:
                        break
                else:
                    raise GmailApiError("history page budget exceeded (>20 pages)")
            except GmailApiError as e:
                if e.status_code == 404:
                    self._log(
                        f"history since {stored} expired — reseeded at {current}; "
                        "gap mail is covered by the backfill window"
                    )
                    self.importer._store_sync_state(current)
                    return
                raise

            remaining = [i for i in candidates if i not in skip]

        incremental_truncated = len(remaining) > self.batch_limit
        batch = list(dict.fromkeys(remaining[: self.batch_limit]))  # dedupe: one
        # id can appear in both messagesAdded and labelsRemoved of a diff
        completed = False
        backfill_exhausted: Optional[bool] = None
        backfill_before_next: Optional[str] = None  # tightened horizon cursor
        yielded_ids: set[str] = set()
        try:
            realized_at = datetime.now(timezone.utc).isoformat()
            for item in self._fetch_batch(api, batch):
                yielded_ids.add(item["metadata"]["gmail_message_id"])
                yield item

            # Stage 2 — reverse-chronological history backfill (RFC-012
            # §4.10 policy) until the oldest email is ingested. Deferred
            # when incremental mail truncated the batch (new mail first),
            # and skipped once the walk has completed. For mailboxes deeper
            # than one run's page budget, the walk continues via a tightening
            # `before:` date cursor (horizon walk, RFC-012 §4.10).
            if not backfill_done and not incremental_truncated:
                walker_skip = skip | yielded_ids
                walker_before = state.get("backfill_before") or None
                walker_batch, backfill_exhausted, walker_boundary = (
                    self.importer._newest_batch_ids(
                        api, walker_skip, self.batch_limit, before=walker_before
                    )
                )
                transient_drops: list[str] = []
                for item in self._fetch_batch(api, walker_batch, transient_drops):
                    yielded_ids.add(item["metadata"]["gmail_message_id"])
                    yield item
                if transient_drops:
                    # A fetch that failed (not a policy/validator skip) in
                    # the draining batch must not complete the walk — retry
                    # those ids on the next run.
                    backfill_exhausted = False

                if walker_boundary and not walker_batch and not yielded_ids:
                    # Horizon stall: the page budget covered only ids that
                    # are all already ingested (nothing new in reach). Tighten
                    # the date cursor past the boundary — its internalDate
                    # (+1 day, done-set absorbs the overlap) bounds the next
                    # walk to the not-yet-covered, older region.
                    try:
                        boundary_msg = api.get_message(walker_boundary)
                        internal_ms = boundary_msg.get("internalDate")
                        boundary_dt = datetime.fromtimestamp(
                            int(internal_ms) / 1000, tz=timezone.utc
                        )
                        next_before = (boundary_dt + timedelta(days=1)).strftime(
                            "%Y/%m/%d"
                        )
                        backfill_before_next = next_before
                        self._log(
                            f"backfill reached the enumeration horizon — "
                            f"tightening the date cursor to before {next_before}"
                        )
                    except Exception as e:  # noqa: BLE001 — defer, retry next run
                        logger.warning(
                            "Horizon tightening failed for %s (%s) — deferred",
                            walker_boundary, e,
                        )

            # Stage 3 — attachments (RFC-012 §4.9: binaries joined the
            # provider contract 2026-11-20, retiring the standalone CLI
            # pass). Coverage skip keeps the scan O(new mail); the yielded
            # dicts flow through the runtime's fact seam, so the operator's
            # scope policy (RFC-011 §4.8, per-account override included)
            # stamps them like every other fact — the reason this pass
            # exists as yields rather than direct soil writes.
            for item in self._attachments_stage(api):
                yield item

            completed = True
        finally:
            # Advance the cursor only on full, clean consumption: by the time
            # the runtime asks for the item past the end, everything yielded
            # is already committed in Soil by the runtime itself.
            if completed and len(remaining) <= len(batch):
                self.importer._store_sync_state(
                    current,
                    backfill=(
                        "complete" if backfill_exhausted
                        else state.get("backfill", "running")
                    ),
                    backfill_before=backfill_before_next,
                )

    def _fetch_batch(
        self, api: GmailApiClient, message_ids: list[str],
        transient_drops: Optional[list[str]] = None,
    ) -> Iterator[dict]:
        """Fetch and translate message ids into runtime-normalized dicts.

        Yields nothing for ids that violate the ingestion policy (all mail
        except what is currently in TRASH or SPAM, evaluated at fetch time)
        or fail validation. Ids whose FETCH failed (lifecycle/transient) are
        reported via `transient_drops` when that list is passed — the
        backfill stage uses this to avoid declaring completion with holes;
        they are retried via history re-diff or the next backfill pass.
        """
        realized_at = datetime.now(timezone.utc).isoformat()
        for message_id in message_ids:
            try:
                message = api.get_message(message_id)
            except Exception:
                if transient_drops is not None:
                    transient_drops.append(message_id)
                continue  # lifecycle/transient — retried via history re-diff
            labels = message.get("labelIds") or []
            if "TRASH" in labels or "SPAM" in labels:
                continue
            item = self.importer.translate_gmail_to_email(
                message, realized_at=realized_at
            )
            if item is None:
                continue
            yield {
                "type": item["_type"],
                "data": item["data"],
                "metadata": item["metadata"],
                "canonical_at": item["canonical_at"],
                "realized_at": item["realized_at"],
            }

    def _attachments_stage(self, api: GmailApiClient) -> Iterator[dict]:
        """Yield this account's uncovered attachments as runtime dicts.

        The extractor reuses the run's already-unlocked client (no extra
        HACM KDF — the old CLI pass paid one per process), and its scan is
        strictly account-scoped: a per-account run covers only emails it
        owns. Steady state (everything covered) touches no network at all.
        """
        from gmail_importer.attachments import AttachmentExtractor, AttachmentStore

        def factory(subject: Optional[str]):
            if subject is not None and subject.lower() != self.account.lower():
                raise GmailApiError(
                    f"attachment scan matched foreign account {subject!r} "
                    f"(this run is {self.account!r})",
                    status_code=403,
                )
            return api

        store = AttachmentStore(self.attachment_store_root)
        extractor = AttachmentExtractor(
            self._soil,
            store,
            factory,
            verbose=False,
            default_account=self.account,
        )
        for item in extractor.iter_uncovered(account_filter=self.account):
            yield item
        stats = extractor.stats
        if any(stats[k] for k in ("downloaded", "errors", "auth_skipped", "attachments_missing")):
            self._log(
                "attachments: scanned={emails_scanned} covered={emails_covered} "
                "downloaded={downloaded} missing={attachments_missing} "
                "auth_skipped={auth_skipped} errors={errors}".format(**stats)
            )

    def create_relations(self, fact) -> list:
        """Containment + threading: an Attachment fact links `contained_by`
        its email; an Email fact gets replies_to when it replies to a
        stored mail — and, reverse-linked, when stored replies await this
        fact.

        The second direction exists because reverse-chronological ingestion
        (RFC-012 §4.10) stores a reply BEFORE its parent: when the parent
        finally lands, no one would otherwise re-link the waiting reply.
        This makes threading eventual-consistent under any ingest order.
        """
        relations: list = []

        from system.soil import generate_soil_uuid
        from system.soil.relation import SystemRelation

        days = (datetime.now(timezone.utc) - _RELATION_EPOCH).days

        def _relation(kind: str, source: str, target: str) -> SystemRelation:
            return SystemRelation(
                uuid=generate_soil_uuid(),
                kind=kind,
                source=source,
                source_type="item",
                target=target,
                target_type="item",
                created_at=days,
                evidence=None,
                metadata=None,
            )

        # Attachments (RFC-012 §4.9): contained_by the email that carried
        # them — same relation the retired CLI pass used to create.
        if (getattr(fact, "_type", "") or "") == "Attachment":
            parent = (fact.metadata or {}).get("email_uuid")
            if parent:
                relations.append(_relation("contained_by", fact.uuid, parent))
            return relations

        data = fact.data or {}
        relations: list = []

        from system.soil import generate_soil_uuid
        from system.soil.relation import SystemRelation

        days = (datetime.now(timezone.utc) - _RELATION_EPOCH).days

        parent = data.get("in_reply_to")
        if not parent and data.get("references"):
            parent = data["references"][-1]

        with self._soil as soil:
            conn = soil._get_connection()
            # 1. This fact replies to a stored mail: link fact -> parent.
            #    (stored rfc_message_id is bracket-normalized; in_reply_to is not)
            if parent:
                normalized = f"<{parent.strip('<>')}>"
                row = conn.execute(
                    """SELECT uuid FROM item WHERE _type = 'Email'
                       AND json_extract(data, '$.rfc_message_id') = ?""",
                    (normalized,),
                ).fetchone()
                if row is not None:
                    relations.append(_relation("replies_to", fact.uuid, row["uuid"]))

            # 2. Stored replies await THIS fact (it is their parent):
            #    link each stored reply -> this fact.
            own_id = data.get("rfc_message_id")
            if own_id:
                stripped = own_id.strip("<>")
                rows = conn.execute(
                    """SELECT uuid FROM item WHERE _type = 'Email'
                       AND (json_extract(data, '$.in_reply_to') = ?
                            OR EXISTS (SELECT 1 FROM json_each(data, '$.references')
                                       WHERE value = ?))""",
                    (stripped, stripped),
                ).fetchall()
                for row in rows:
                    relations.append(_relation("replies_to", row["uuid"], fact.uuid))

        return relations

    # ------------------------------------------------------------------

    def _ingested_gmail_ids(self) -> set[str]:
        # This account's done-set only (RFC-012 §4.6): gmail message ids are
        # per-mailbox namespaces. Projected in SQL (same pattern as the tada
        # provider's id sets): materializing every Email fact here cost
        # ~700 MB per hourly sync and left the worker's arenas at that size
        # between runs.
        with self._soil as soil:
            rows = soil._get_connection().execute(
                """SELECT json_extract(metadata, '$.gmail_message_id') AS mid
                   FROM item
                   WHERE _type = 'Email'
                     AND json_extract(metadata, '$.account') = ?""",
                (self.account,),
            ).fetchall()
            mids = {row["mid"] for row in rows if row["mid"]}
            if mids:
                return mids
            # Pre-backfill defense (RFC-012 §8 M5): an empty filtered scan is
            # ambiguous — either this account has ingested nothing yet, or
            # the legacy facts were never backfilled with metadata.account.
            # Only a corpus with NO attributed gmail fact anywhere is the
            # legacy case (any attributed fact means backfill happened and
            # this account genuinely starts empty); claiming it unfiltered
            # keeps deploy order from re-ingesting the whole mailbox.
            row = soil._get_connection().execute(
                """SELECT COUNT(*) AS n FROM item
                   WHERE _type = 'Email'
                     AND json_extract(metadata, '$.gmail_message_id') IS NOT NULL
                     AND json_extract(metadata, '$.account') IS NOT NULL"""
            ).fetchone()
            if row["n"]:
                return set()
            legacy = soil._get_connection().execute(
                """SELECT json_extract(metadata, '$.gmail_message_id') AS mid
                   FROM item
                   WHERE _type = 'Email'"""
            ).fetchall()
            return {row_["mid"] for row_ in legacy if row_["mid"]}


def build_client_for_account(
    subject: str, hacm_path: str, passphrase: Optional[str]
) -> GmailApiClient:
    """An API client for one account, via the HACM-stored refresh token.

    The GmailOAuthClient is constructed per call here; callers covering
    MANY accounts per process (the CLI attachments pass) should construct
    one oauth client and reuse it across accounts instead (INV-5: one
    Argon2id unlock per run, never one per account).
    """
    oauth_cls = GmailImporter._load_oauth_client()
    oauth = oauth_cls(hacm_path, passphrase=passphrase)
    tokens = oauth.refresh_access_token(subject)
    return GmailApiClient(tokens["access_token"])
