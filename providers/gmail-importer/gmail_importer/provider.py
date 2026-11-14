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

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from gmail_importer.gmail_api import GmailApiClient, GmailApiError
from gmail_importer.importer import GmailImporter

_RELATION_EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc)

_DEFAULT_HACM_PATH = "~/.local/share/memogarden/hacm/credentials.enc"
_DEFAULT_PASSPHRASE_FILE = "~/.local/share/memogarden/hacm/passphrase"
_DEFAULT_BATCH_LIMIT = 500


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
        """Yield Email facts for mail NEW since the stored historyId.

        Args:
            since: Ignored — the historyId in the sync state is the cursor
                (a timestamp cannot address Gmail's change log).

        Cursor advancement: the sync state advances when the runtime
        consumes the full batch (this generator runs to completion), and
        stays untouched when the batch was truncated by batch_limit (the
        remainder re-delivers next sync; the runtime's done-set absorbs
        what already landed) or when iteration aborted early.
        """
        api: GmailApiClient = self.importer._build_api_client()
        done = self._ingested_gmail_ids()
        excluded = self.importer._load_excluded_ids()
        skip = done | excluded

        state = self.importer._load_sync_state()
        profile = api.get_profile()
        current = str(profile.get("historyId") or "")
        stored = state.get("history_id")

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

        batch = remaining[: self.batch_limit]
        completed = False
        try:
            realized_at = datetime.now(timezone.utc).isoformat()
            for message_id in batch:
                try:
                    message = api.get_message(message_id)
                except Exception:
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
            completed = True
        finally:
            # Advance the cursor only on full, clean consumption: by the time
            # the runtime asks for the item past the end, everything yielded
            # is already committed in Soil by the runtime itself.
            if completed and len(remaining) <= len(batch):
                self.importer._store_sync_state(current)

    def create_relations(self, fact) -> list:
        """Threading: replies_to when the fact replies to a stored mail."""
        data = fact.data or {}
        parent = data.get("in_reply_to")
        if not parent and data.get("references"):
            parent = data["references"][-1]
        if not parent:
            return []
        # stored rfc_message_id is bracket-normalized; in_reply_to is not
        parent = f"<{parent.strip('<>')}>"

        from system.soil import generate_soil_uuid
        from system.soil.relation import SystemRelation

        with self._soil as soil:
            row = soil._get_connection().execute(
                """SELECT uuid FROM item WHERE _type = 'Email'
                   AND json_extract(data, '$.rfc_message_id') = ?""",
                (parent,),
            ).fetchone()
        if row is None:
            return []

        days = (datetime.now(timezone.utc) - _RELATION_EPOCH).days
        return [
            SystemRelation(
                uuid=generate_soil_uuid(),
                kind="replies_to",
                source=fact.uuid,
                source_type="item",
                target=row["uuid"],
                target_type="item",
                created_at=days,
                evidence=None,
                metadata=None,
            )
        ]

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
