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

_DEFAULTS = {
    "hacm_path": "~/.local/share/memogarden/hacm/credentials.enc",
    "passphrase_file": "~/.local/share/memogarden/hacm/passphrase",
    "sync_state_path": "~/.local/share/memogarden/gmail-sync-state.json",
    "excluded_ids_path": "~/.local/share/memogarden/gmail-excluded-ids.json",
    "batch_limit": 500,
}


class GmailProvider:
    """Provider Protocol implementation over the gmail-importer machinery."""

    def __init__(self, config: dict):
        merged = {**_DEFAULTS, **(config or {})}
        self.account = merged["account"]
        self.batch_limit = int(merged.get("batch_limit", _DEFAULTS["batch_limit"]))
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
        pf = Path(config.get("passphrase_file", _DEFAULTS["passphrase_file"])).expanduser()
        return pf.read_text().strip() if pf.exists() else None

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
        with self._soil as soil:
            return {
                (f.metadata or {}).get("gmail_message_id")
                for f in soil.list_items(_type="Email", limit=1_000_000)
                if (f.metadata or {}).get("gmail_message_id")
            }
