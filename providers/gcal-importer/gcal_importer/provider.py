"""GoogleCalendarProvider — mirrors concrete events into Core Event
entities (RFC-014; registered as `gcal`).

Contract: ENTITY_UPSERTS provider. sync() yields dicts of the RFC-014 §3
shape — {"op": "upsert_entity", "type": "Event", "key": {...}, "data":
{...}, "metadata": {...}} — and the runtime stores (natural-key lookup →
etag no-op/update/create + scope policy + per-item commit). The runtime's
`since` timestamp is accepted but ignored: the cursor is Google's own
syncToken, provider-managed in a per-subject state file (gmail historyId
precedent).

Engine (RFC-014 §4, RFC-012 §4.10 discipline):
1. Incremental first, per configured calendar: events.list(syncToken,
   singleEvents=true) — the identical parameter set every call. Cancelled
   tombstones for known events yield stamp-only upserts (data untouched);
   unknown tombstones are skipped (nothing to un-memorize).
2. Seeds for calendars without a stored token: full listing, NO timeMin
   (full history, the settled backfill decision), page-budgeted — defer,
   never fail; the token persists only when the whole listing walked.
   Past-at-seed events carry metadata.captured_after_fact (review triage
   marker).
3. 410 GONE: clear that calendar's token, reseed inline, force the audit.
4. Audit (monthly + forced post-reseed): full listing per calendar diffed
   against the etag map — silent vanishings (master deletion tombstones
   only name the master we never store) surface ONLY here, and newly
   materialized instances appear only here.

Cursor advancement: state persists only when the generator runs to
completion (finally-block discipline, gmail/tada precedent); everything
short of that re-delivers next run and the etag map no-ops it away.
Budget overruns defer — they skip work inside a completing generator,
never abandon it (the token of a truncated walk simply stays put).

Credential cost (INV-5): the HACM store unlock (~1 GiB Argon2id) happens
lazily on the first API call, so a run that defers before any API traffic
never pays it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from gcal_importer.calendar_api import GCalApiError, GoogleCalendarClient
from gcal_importer.state import default_state_path, load_state, store_state

_DEFAULTS = {
    "calendar_ids": [],        # empty → owner-role calendars (RFC-014 §4.1)
    "batch_limit": 500,        # upserts per run before deferral
    "page_budget": 40,         # events.list pages per run
    "participants_max": 50,
    "audit_interval_days": 30,
    "sync_state_path": None,   # default: per-subject path (state.default_state_path)
    "hacm_path": "~/.local/share/memogarden/hacm/credentials.enc",
    "passphrase_file": "~/.local/share/memogarden/hacm/passphrase",
    "request_timeout": 30.0,
}

_DETECTION_SYNC = "sync_tombstone"
_DETECTION_AUDIT = "audit_diff"


class GoogleCalendarProvider:
    """Provider Protocol implementation over the Calendar API (read-only)."""

    PLATFORMS = ["google"]
    ENTITY_UPSERTS = True

    def __init__(self, config: dict, account: dict | None = None):
        merged = {**_DEFAULTS, **(config or {})}
        expand = lambda p: str(Path(p).expanduser())
        self._account_id = account["subject"] if account else "local"
        self._calendar_ids = [str(c) for c in (merged["calendar_ids"] or [])]
        self._batch_limit = int(merged["batch_limit"])
        self._page_budget = int(merged["page_budget"])
        self._participants_max = int(merged["participants_max"])
        self._audit_interval_days = int(merged["audit_interval_days"])
        self._timeout = float(merged["request_timeout"])
        self._hacm_path = expand(merged["hacm_path"])
        self._passphrase_file = expand(merged["passphrase_file"])
        # M5 lesson: the default is subject-qualified precisely so a
        # provider-level config never shares one cursor across accounts.
        self._state_path = expand(
            merged["sync_state_path"] or default_state_path(self._account_id)
        )
        self._client: GoogleCalendarClient | None = None
        self._audit_truncated = False

    def _log(self, msg: str) -> None:
        print(f"[gcal provider] {msg}")

    # ------------------------------------------------------------------
    # Provider Protocol
    # ------------------------------------------------------------------

    def sync(self, since: str | None = None) -> Iterator[dict]:
        """Yield entity upserts for calendar changes since the stored tokens.

        Args:
            since: Ignored — syncTokens are the cursor (like gmail's
                historyId; a timestamp cannot address Google's change log).
        """
        client = self._get_client()
        calendars = self._select_calendars(client)
        state = load_state(self._state_path)
        if state.get("account") not in (None, self._account_id):
            # Subject mismatch (reused/copied state file) — reseed
            # everything rather than poison cursors (gmail INV-3 discipline).
            self._log("state file belongs to another account — reseeding")
            state = {}
        state["account"] = self._account_id
        cal_state: dict = state.setdefault("calendars", {})
        etag_map = self._etag_map()
        budget = {"pages": self._page_budget, "upserts": self._batch_limit}

        completed = False
        try:
            for calendar in calendars:
                entry = cal_state.get(calendar["id"]) or {}
                cal_state[calendar["id"]] = entry
                if entry.get("sync_token"):
                    try:
                        yield from self._incremental(
                            client, calendar, entry, etag_map, budget
                        )
                        continue
                    except GCalApiError as e:
                        if not e.gone:
                            raise
                        # 410 fullSyncRequired: token expired — clear and
                        # reseed inline, force the audit diff (RFC-014 §4.2).
                        self._log(
                            f"{calendar['id']}: sync token expired (410) — reseeding"
                        )
                        entry["sync_token"] = None
                        state["audit_force"] = True
                yield from self._seed(client, calendar, entry, etag_map, budget)

            # Start the audit clock at first seed: a fresh corpus has
            # nothing to diff against, so the first audit runs one
            # interval later (forced audits bypass the clock entirely).
            state.setdefault("audit_last", self._today())
            if self._audit_due(state):
                self._audit_truncated = False
                yield from self._audit(client, calendars, etag_map, budget)
                if self._audit_truncated:
                    # Deferred, never failed: an incomplete audit records
                    # nothing and re-runs next sync (audit_last untouched,
                    # a forced flag preserved) — a truncated audit must
                    # never mark itself done (review P0-1).
                    self._log("audit truncated — re-running next sync")
                else:
                    state["audit_last"] = self._today()
                    state.pop("audit_force", None)
            completed = True
        finally:
            if completed:
                state["updated_at"] = self._now()
                store_state(self._state_path, state)

    @staticmethod
    def verify_account(account: dict, config: dict) -> dict:
        """Server-side credential probe (RFC-012 §6.1 / RFC-014 §5).

        Subject cross-check without Gmail: the account's own primary
        calendar appears in calendarList with id == the account email.
        Nothing is persisted on a mismatch — the probe just fails.
        """
        try:
            client = _client_for_subject(
                account["subject"],
                config.get("hacm_path"),
                config.get("passphrase_file"),
            )
            entries = client.calendar_list()
        except Exception as e:  # noqa: BLE001 — a broken probe is a failed probe
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        ok = any(
            entry.get("id", "").casefold() == account["subject"].casefold()
            and entry.get("primary")
            for entry in entries
        )
        return {
            "ok": ok,
            "error": None if ok else "primary calendar not found in calendarList "
            "(subject mismatch or no calendars)",
        }

    # ------------------------------------------------------------------
    # Sync stages
    # ------------------------------------------------------------------

    def _incremental(
        self, client, calendar, entry, etag_map, budget
    ) -> Iterator[dict]:
        """syncToken delta for one calendar; tombstones → removal stamps."""
        cid = calendar["id"]
        known = etag_map.get(cid, {})
        params = {
            "singleEvents": "true",
            "maxResults": 250,
            "syncToken": entry["sync_token"],
        }
        processed: set[str] = set()
        page_token = None
        while budget["pages"] > 0:
            call_params = dict(params)
            if page_token:
                call_params["pageToken"] = page_token
            body = client.events_list_page(cid, call_params)
            budget["pages"] -= 1
            for item in body.get("items", []):
                event_id = item.get("id")
                if not event_id or event_id in processed:
                    continue
                processed.add(event_id)
                if item.get("status") == "cancelled":
                    stamp = self._removal_stamp(cid, event_id, known, _DETECTION_SYNC)
                    if stamp is None:
                        continue
                    if budget["upserts"] <= 0:
                        self._log(f"{cid}: incremental truncated by upsert budget — deferring")
                        return
                    budget["upserts"] -= 1
                    yield stamp
                    continue
                upsert = self._event_upsert(item, calendar, mark_captured=False)
                if upsert is None:
                    continue
                if budget["upserts"] <= 0:
                    self._log(f"{cid}: incremental truncated by upsert budget — deferring")
                    return
                budget["upserts"] -= 1
                yield upsert
            new_token = body.get("nextSyncToken")
            page_token = body.get("nextPageToken")
            if new_token:
                # The delta is fully consumed — the token may advance.
                # (State itself persists only when the whole run completes.)
                entry["sync_token"] = new_token
                return
            if not page_token:
                return
        self._log(f"{cid}: incremental truncated by page budget — deferring remainder")

    def _seed(self, client, calendar, entry, etag_map, budget) -> Iterator[dict]:
        """Full listing (no timeMin — full history); token on completion.

        Known-and-unchanged events are skipped client-side: a truncated
        seed re-walks from page 1 next run, and without the skip every
        continuation run would re-deliver the same known events and burn
        its whole batch budget before reaching anything new (observed
        live: first run created 500, run 2 would have stalled forever).
        The runtime etag no-op absorbs correctness; this restores the
        gmail done-set economics, fed by the Core projection.
        """
        cid = calendar["id"]
        known = etag_map.get(cid, {})
        params = {"singleEvents": "true", "maxResults": 250}
        items, sync_token, walked_all, pages_used = client.events_list_all(
            cid, params, page_budget=max(budget["pages"], 0)
        )
        budget["pages"] -= pages_used
        for item in items:
            if item.get("status") == "cancelled":
                # A full listing without showDeleted omits cancelled
                # events; if one slips through anyway, skip it — the
                # seed stores living events only.
                continue
            known_entry = known.get(item.get("id"))
            if (
                known_entry is not None
                and known_entry[0] == item.get("etag")
                and known_entry[1] is None
            ):
                continue  # known & unchanged (not a resurrection — skip)
            upsert = self._event_upsert(item, calendar, mark_captured=True)
            if upsert is None:
                continue
            if budget["upserts"] <= 0:
                self._log(f"{cid}: seed truncated by upsert budget — deferring")
                return
            budget["upserts"] -= 1
            yield upsert
        if not walked_all or not sync_token:
            self._log(f"{cid}: seed truncated by upsert/page budget — token NOT stored")
            return
        entry["sync_token"] = sync_token
        entry["seeded"] = True

    def _audit(self, client, calendars, etag_map, budget) -> Iterator[dict]:
        """Full listing diff per calendar: silent vanishings → stamps;
        newly materialized instances → ordinary upserts (etag noops
        absorb the known ones).

        Truncation discipline: the vanishings diff runs ONLY on a
        complete listing — a partial `seen` would stamp living events as
        vanished, and that stamp is unrepairable (later audits skip
        already-cancelled entries; Google never re-delivers unchanged
        living events). Any truncation sets `_audit_truncated` so sync()
        leaves the audit due for the next run.
        """
        for calendar in calendars:
            cid = calendar["id"]
            known = etag_map.get(cid, {})
            params = {"singleEvents": "true", "maxResults": 250}
            items, _token, walked, pages_used = client.events_list_all(
                cid, params, page_budget=max(budget["pages"], 0)
            )
            budget["pages"] -= pages_used
            if not walked:
                self._audit_truncated = True
                self._log(f"{cid}: audit listing truncated by page budget — deferred")
                continue
            seen: set[str] = set()
            exhausted = False
            for item in items:
                event_id = item.get("id")
                if not event_id:
                    continue
                seen.add(event_id)
                if item.get("status") == "cancelled":
                    stamp = self._removal_stamp(cid, event_id, known, _DETECTION_AUDIT)
                    if stamp is None:
                        continue
                    if budget["upserts"] <= 0:
                        exhausted = True
                        break
                    budget["upserts"] -= 1
                    yield stamp
                    continue
                known_entry = known.get(event_id)
                if (
                    known_entry is not None
                    and known_entry[0] == item.get("etag")
                    and known_entry[1] is None
                ):
                    # Known & unchanged: the runtime would no-op the
                    # upsert — don't burn audit budget re-delivering a
                    # full corpus every month (seed-skip precedent).
                    continue
                upsert = self._event_upsert(item, calendar, mark_captured=True)
                if upsert is None:
                    continue
                if budget["upserts"] <= 0:
                    exhausted = True
                    break
                budget["upserts"] -= 1
                yield upsert
            if exhausted:
                self._audit_truncated = True
                self._log(f"{cid}: audit truncated by upsert budget — deferred")
                return
            # Vanished: complete listing in hand; stored, not already
            # cancelled, absent from it.
            for event_id in sorted(known):
                stored_etag, stored_status = known[event_id]
                if event_id in seen or stored_status == "cancelled":
                    continue
                if budget["upserts"] <= 0:
                    self._audit_truncated = True
                    self._log(f"{cid}: audit truncated by upsert budget — deferred")
                    return
                budget["upserts"] -= 1
                yield self._removal_upsert(
                    cid, event_id, stored_etag, _DETECTION_AUDIT
                )

    # ------------------------------------------------------------------
    # Translation
    # ------------------------------------------------------------------

    def _event_upsert(self, item: dict, calendar: dict, mark_captured: bool) -> dict | None:
        """Map a Google event resource to an Event entity upsert.

        Returns None for unstoreable items (no start time) — skipped
        visibly, never a fact-less shell.
        """
        cid = calendar["id"]
        event_id = item.get("id")
        if not event_id:
            return None
        start = item.get("start") or {}
        end = item.get("end") or {}
        starts_at = self._moment(start)
        if starts_at is None:
            self._log(f"{cid}/{event_id}: no start time — skipped")
            return None
        attendees = item.get("attendees") or []
        participants = [
            a.get("email") or a.get("displayName") or a.get("id") or ""
            for a in attendees
        ]
        participants = [p for p in participants if p]
        organizer = item.get("organizer") or {}
        organizer_id = (
            organizer.get("email") or organizer.get("displayName") or organizer.get("id")
        )
        self_response = next(
            (a.get("responseStatus") for a in attendees if a.get("self")), None
        )
        data: dict = {
            "source_id": event_id,
            "starts_at": starts_at,
            "status": item.get("status") or "confirmed",
            # Explicit None for clearable fields (RFC-014 §3.2 merge
            # semantics): Google resources arrive complete on change, so
            # an absent optional field means "cleared at source" — the
            # runtime merge needs the null to clear the stored value.
            "title": item.get("title") or None,
            "description": item.get("description") or None,
            "location": item.get("location") or None,
            "ends_at": self._moment(end),
            "participants": participants[: self._participants_max] if participants else None,
            "organizer": organizer_id,
        }

        metadata: dict = {
            "source": "google_calendar",
            "account": self._account_id,
            "calendar_id": cid,
            "event_id": event_id,
            # Explicit None where the source can clear a value; etag and
            # updated_at are omitted when absent instead (provenance
            # identity — keeping the stored value beats churning it).
            "ical_uid": item.get("iCalUID"),
            "recurring_event_id": item.get("recurringEventId"),
            "original_start_at": self._moment(item.get("originalStartTime") or {}),
            "attendee_count": len(attendees) if attendees else None,
            "self_response": self_response,
            # Resurrection (review P1-1): living payloads clear the
            # removal triple, so an instance Google re-materializes under
            # the same id sheds its stamps via the merge null-clears.
            "source_status": None,
            "removed_at_source": None,
            "detection_source": None,
        }
        if calendar.get("summary"):
            metadata["calendar_summary"] = calendar["summary"]
        if item.get("etag"):
            metadata["etag"] = item["etag"]
        if item.get("updated"):
            metadata["updated_at"] = item["updated"]
        if mark_captured and self._ended_in_past(data):
            metadata["captured_after_fact"] = True
        return {
            "op": "upsert_entity",
            "type": "Event",
            "key": self._key(cid, event_id),
            "data": data,
            "metadata": metadata,
        }

    def _removal_stamp(
        self, cid: str, event_id: str, known: dict, detection: str
    ) -> dict | None:
        """Stamp-only upsert for a tombstone — None when we never stored
        the event (nothing to un-memorize; gmail excluded-ids equivalent,
        but no sidecar needed: tombstones are one-shot in the stream)."""
        stored = known.get(event_id)
        if stored is None:
            return None
        stored_etag, _status = stored
        return self._removal_upsert(cid, event_id, stored_etag, detection)

    def _removal_upsert(
        self, cid: str, event_id: str, stored_etag: str | None, detection: str
    ) -> dict:
        metadata = {
            "source": "google_calendar",
            "account": self._account_id,
            "calendar_id": cid,
            "source_status": "cancelled",
            "removed_at_source": self._now(),
            "detection_source": detection,
        }
        if stored_etag is not None:
            metadata["etag"] = stored_etag
        return {
            "op": "upsert_entity",
            "type": "Event",
            "key": self._key(cid, event_id),
            "data": {},  # stamp-only: the runtime merge must not clobber beliefs
            "metadata": metadata,
        }

    def _key(self, cid: str, event_id: str) -> dict:
        return {
            "source": "google_calendar",
            "account": self._account_id,
            "calendar_id": cid,
            "source_id": event_id,
        }

    def _moment(self, when: dict) -> str | None:
        """start/end sub-object → UTC datetime ISO, or bare date verbatim.

        Date-only values carry NO time and NO timezone by design (RFC-014
        D5: the missing time IS the all-day ambiguity). Timed values are
        normalized to UTC (tada to_utc_iso precedent); a floating local
        time resolves via the sub-object's timeZone, falling back to UTC
        — never the machine's local zone, silently.
        """
        if not when:
            return None
        date_only = when.get("date")
        if date_only:
            return date_only
        date_time = when.get("dateTime")
        if not date_time:
            return None
        try:
            moment = datetime.fromisoformat(date_time.replace("Z", "+00:00"))
        except ValueError:
            self._log(f"unparseable dateTime {date_time!r} — left verbatim")
            return date_time
        if moment.tzinfo is None:
            tz_name = when.get("timeZone") or "UTC"
            try:
                moment = moment.replace(tzinfo=ZoneInfo(tz_name))
            except Exception:  # noqa: BLE001 — unknown tz falls back to UTC
                moment = moment.replace(tzinfo=UTC)
        return moment.astimezone(UTC).isoformat()

    def _ended_in_past(self, data: dict) -> bool:
        """True when the event definitely ended before now (datetime or
        date-only). Conservative: unparseable/ambiguous cases return False."""
        end = data.get("ends_at") or data.get("starts_at")
        if not end:
            return False
        try:
            if len(end) == 10:  # bare date YYYY-MM-DD
                return datetime.fromisoformat(end).date() < datetime.now(UTC).date()
            moment = datetime.fromisoformat(end.replace("Z", "+00:00"))
            return moment < datetime.now(UTC)
        except ValueError:
            return False

    # ------------------------------------------------------------------
    # Selection, scheduling, storage access
    # ------------------------------------------------------------------

    def _select_calendars(self, client) -> list[dict]:
        entries = client.calendar_list()
        if self._calendar_ids:
            allow = set(self._calendar_ids)
            selected = [e for e in entries if e.get("id") in allow]
        else:
            selected = [e for e in entries if e.get("accessRole") == "owner"]
        if not selected:
            self._log(
                "no calendars selected"
                + (
                    f" (allowlist {self._calendar_ids} matched nothing)"
                    if self._calendar_ids
                    else ""
                )
            )
        return selected

    def _audit_due(self, state: dict) -> bool:
        if state.get("audit_force"):
            return True
        last = state.get("audit_last")
        if not last:
            return False
        try:
            last_day = datetime.fromisoformat(str(last)[:10]).date()
        except ValueError:
            return True
        today = datetime.now(UTC).date()
        return (today - last_day).days >= self._audit_interval_days

    def _etag_map(self) -> dict[str, dict[str, tuple]]:
        """Per-calendar {event_id: (etag, source_status)} projected from the
        Core entity corpus, filtered by account (the 700 MB lesson: scan
        projections, never materialize entities)."""
        from system.core import get_core

        with get_core() as core:
            rows = core._conn.execute(
                """SELECT json_extract(data, '$.calendar_id') AS cal,
                          json_extract(data, '$.source_id') AS sid,
                          json_extract(data, '$.etag') AS etag,
                          json_extract(data, '$.source_status') AS sstatus
                   FROM entity
                   WHERE type = 'Event'
                     AND json_extract(data, '$.account') = ?
                     AND superseded_by IS NULL""",
                (self._account_id,),
            ).fetchall()
        out: dict[str, dict[str, tuple]] = {}
        for row in rows:
            if row["cal"] and row["sid"]:
                out.setdefault(row["cal"], {})[row["sid"]] = (row["etag"], row["sstatus"])
        return out

    # ------------------------------------------------------------------
    # Credential path (lazy — INV-5: one HACM unlock per run at most)
    # ------------------------------------------------------------------

    def _get_client(self) -> GoogleCalendarClient:
        if self._client is None:
            self._client = GoogleCalendarClient(
                self._access_token(), timeout=self._timeout
            )
        return self._client

    def _access_token(self) -> str:
        # Reuse gmail's dash-name loader (reference, don't copy): the
        # google:{subject} HACM entry is shared by design (RFC-012 §3);
        # revoking it kills gmail + gcal together — the documented footgun.
        from gmail_importer.provider import GmailImporter

        oauth_cls = GmailImporter._load_oauth_client()
        oauth = oauth_cls(self._hacm_path, passphrase=self._passphrase())
        tokens = oauth.refresh_access_token(self._account_id)
        return tokens["access_token"]

    def _passphrase(self) -> str | None:
        p = Path(self._passphrase_file)
        return p.read_text().strip() if p.exists() else None

    # ------------------------------------------------------------------
    # Clock seams (tests fix these)
    # ------------------------------------------------------------------

    def _now(self) -> str:
        fixed = getattr(self, "_fixed_now", None)
        return fixed if fixed is not None else datetime.now(UTC).isoformat()

    def _today(self) -> str:
        fixed = getattr(self, "_fixed_now", None)
        if fixed is not None:
            return str(fixed)[:10]
        return datetime.now(UTC).date().isoformat()


def _client_for_subject(
    subject: str, hacm_path: str | None = None, passphrase_file: str | None = None
) -> GoogleCalendarClient:
    """Verify-path client construction (static context — no self)."""
    from gmail_importer.provider import GmailImporter

    oauth_cls = GmailImporter._load_oauth_client()
    hacm = str(Path(hacm_path).expanduser()) if hacm_path else \
        str(Path("~/.local/share/memogarden/hacm/credentials.enc").expanduser())
    passphrase = None
    pf = Path(passphrase_file).expanduser() if passphrase_file else \
        Path("~/.local/share/memogarden/hacm/passphrase").expanduser()
    if pf.exists():
        passphrase = pf.read_text().strip()
    oauth = oauth_cls(hacm, passphrase=passphrase)
    tokens = oauth.refresh_access_token(subject)
    return GoogleCalendarClient(tokens["access_token"])
