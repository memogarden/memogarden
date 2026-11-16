"""Fixtures for gcal_importer tests: a scripted fake client, DB-free.

The provider's only storage touchpoint is the etag map (Core entity
projection); tests override `_etag_map` so no database is needed. The
auth path is never exercised — tests inject `provider._client` directly
(the lazy HACM unlock stays untested here by design, INV-5).
"""

import json
from pathlib import Path

import pytest

from gcal_importer.calendar_api import GoogleCalendarClient
from gcal_importer.provider import GoogleCalendarProvider


class FakeCalendarClient(GoogleCalendarClient):
    """Scripted client: calendarList fixture + a per-calendar page queue.

    Each events_list_page call pops the next scripted response for that
    calendar (each response is a raw API page dict). When the queue is
    empty it returns a closed empty page (nextSyncToken absent, no
    nextPageToken) so walks always terminate.
    """

    def __init__(self, calendars=None, pages=None):
        super().__init__(access_token="fake-token")
        self.calendar_entries = calendars or []
        self.pages = pages or {}
        self.calls: list[tuple[str, dict]] = []

    def calendar_list(self):
        return [dict(c) for c in self.calendar_entries]

    def events_list_page(self, calendar_id, params):
        self.calls.append((calendar_id, dict(params)))
        queue = self.pages.get(calendar_id) or []
        if queue:
            return queue.pop(0)
        return {"items": []}


def make_event(event_id, start, end=None, status="confirmed", etag=None, **extra):
    item = {
        "id": event_id,
        "status": status,
        "start": start,
        "end": end or start,
    }
    if etag:
        item["etag"] = etag
    item.update(extra)
    return item


@pytest.fixture
def tmp_state(tmp_path):
    return str(tmp_path / "state" / "gcal-sync-state--me@gmail.com.json")


@pytest.fixture
def owner_calendar():
    return {"id": "me@gmail.com", "summary": "Personal", "accessRole": "owner"}


@pytest.fixture
def provider(tmp_state, owner_calendar):
    """Provider wired to a fake client; etag map overridden per test via
    the `stored` fixture (set provider._stored to a {cid: {sid: (etag,
    status)}} dict, or leave empty for a fresh corpus)."""
    p = GoogleCalendarProvider(
        config={"sync_state_path": tmp_state, "page_budget": 10, "batch_limit": 100},
        account={"subject": "me@gmail.com", "platform": "google",
                 "credential_ref": "google:me@gmail.com", "config": {}},
    )
    p._client = FakeCalendarClient(calendars=[owner_calendar])
    p._stored = {}
    p._etag_map = lambda: dict(p._stored)
    p._fixed_now = "2026-11-16T12:00:00+00:00"
    return p


def read_state(path):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else None
