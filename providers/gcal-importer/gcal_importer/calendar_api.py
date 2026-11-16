"""Thin Google Calendar API client (stdlib only — gmail/tada precedent).

No google-api-python-client: this workspace's Google providers speak REST
with stdlib urllib and map errors onto provider-typed exceptions. Only the
read surface the RFC-014 engine needs is implemented — calendar.readonly
is the entire consent surface.

API behaviors that shape this interface (verified against the events.list
reference, 2026-11-16):
- nextSyncToken appears ONLY on the last page; walk pages until it does.
- 410 GONE (fullSyncRequired) = the sync token expired → full reseed.
- With syncToken, deleted events ALWAYS appear (status='cancelled') and
  showDeleted=False is rejected with 400.
- singleEvents=true IS permitted with syncToken (the forbidden list is
  iCalUID, orderBy, q, privateExtendedProperty, sharedExtendedProperty,
  timeMin, timeMax, updatedMin), but incremental calls must reuse the
  identical parameter set of the initial sync.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

_BASE = "https://www.googleapis.com/calendar/v3"


class GCalApiError(Exception):
    """A Google Calendar API failure with status and auth classification.

    auth_failure=True marks credential-shaped failures (401/403, or
    Google's invalid_grant body) — the provider runtime parks the account
    in needs_reauth on these instead of failing the heartbeat hourly
    (RFC-012 §4.2). status 410 sets `gone` (sync token expired; the
    engine reseeds, RFC-014 §4.2).
    """

    def __init__(self, message: str, status_code: int | None = None, auth_failure: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.auth_failure = auth_failure
        self.gone = status_code == 410


class GoogleCalendarClient:
    """Read-only client over calendarList + events endpoints."""

    def __init__(self, access_token: str, timeout: float = 30.0):
        self._token = access_token
        self._timeout = timeout

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{_BASE}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            auth_failure = e.code in (401, 403) or "invalid_grant" in body
            raise GCalApiError(
                f"Google Calendar API {e.code} for {path}: {body[:300]}",
                status_code=e.code,
                auth_failure=auth_failure,
            ) from None
        except urllib.error.URLError as e:
            raise GCalApiError(f"transport error for {path}: {e.reason}") from None

    def calendar_list(self) -> list[dict]:
        """All calendarList entries (paged). Each carries id, summary,
        accessRole — the selection surface for the owner-role default."""
        entries: list[dict] = []
        page_token = None
        while True:
            params = {"maxResults": 250}
            if page_token:
                params["pageToken"] = page_token
            body = self._get("/users/me/calendarList", params)
            entries.extend(body.get("items", []))
            page_token = body.get("nextPageToken")
            if not page_token:
                return entries

    def events_list_page(self, calendar_id: str, params: dict) -> dict:
        """One raw events.list page.

        Params pass through verbatim on purpose: the incremental contract
        requires the identical parameter set on every call ("avoid
        undefined behavior"), so the caller owns the params dict.
        """
        quoted = urllib.parse.quote(calendar_id, safe="")
        return self._get(f"/calendars/{quoted}/events", params)

    def events_list_all(
        self, calendar_id: str, params: dict, page_budget: int | None = None
    ) -> tuple[list[dict], str | None, bool, int]:
        """Walk pages until nextSyncToken (present on the last page only).

        Returns (items, next_sync_token, completed, pages_used).
        completed=False means the page budget ran out before the listing
        ended — the caller must treat the walk as truncated (no token
        persistence; the remainder re-delivers on the next run and the
        etag map absorbs it).
        """
        items: list[dict] = []
        work = dict(params)
        pages = 0
        while True:
            if page_budget is not None and pages >= page_budget:
                return items, None, False, pages
            body = self.events_list_page(calendar_id, work)
            pages += 1
            items.extend(body.get("items", []))
            sync_token = body.get("nextSyncToken")
            if sync_token:
                return items, sync_token, True, pages
            page_token = body.get("nextPageToken")
            if not page_token:
                # No sync token and no more pages: a full listing without
                # a token budget (audit mode) ends here.
                return items, None, True, pages
            work = {**params, "pageToken": page_token}
