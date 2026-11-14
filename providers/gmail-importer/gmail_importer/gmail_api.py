"""Gmail API client for the gmail-importer.

A thin `requests`-based client (no google-api-python-client) plus the
oldest-window search used to fetch the N oldest messages of a mailbox.

Gmail's messages.list returns newest-first only, and its resultSizeEstimate
is unreliable in BOTH directions (observed: an 11,750-message mailbox
estimated as ~3,000). All decisions here therefore use ACTUAL enumerability
— bounded page walks — never estimates. Estimates are not even fetched.
"""

from __future__ import annotations

import calendar
import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

# Gmail launched 2004-04-01; no message can predate it.
_GMAIL_LAUNCH_MS = calendar.timegm((2004, 4, 1, 0, 0, 0)) * 1000

_LIST_PAGE_SIZE = 500
_DAY_MS = 86_400_000
_ENUM_PAGES = 12  # 6,000 messages — a full window any personal mailbox prefix fits in


class GmailApiError(RuntimeError):
    """Raised when the Gmail API returns an error we can't handle.

    status_code carries the HTTP status when the error came from a
    response (e.g. 404 for expired history in history.list).

    auth_failure (RFC-012 §4.2): True when the failure is credential-shaped
    (expired/revoked token, missing or rejected refresh token, mailbox
    mismatch). The provider runtime parks accounts whose run raises an
    auth_failure-marked exception into needs_reauth instead of retrying
    them on every heartbeat.
    """

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        auth_failure: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.auth_failure = auth_failure


class GmailApiClient:
    """Minimal Gmail API v1 client authorized by an OAuth access token."""

    def __init__(self, access_token: str, timeout: int = 30):
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {access_token}"
        self._timeout = timeout
        self.call_count = 0

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        self.call_count += 1
        response = self._session.get(
            f"{GMAIL_API_BASE}{path}", params=params, timeout=self._timeout
        )
        if response.status_code == 401:
            raise GmailApiError(
                "Gmail API returned 401: access token expired or scopes not granted. "
                "Re-run the OAuth flow if the refresh token was revoked.",
                auth_failure=True,
            )
        if response.status_code != 200:
            raise GmailApiError(
                f"Gmail API error {response.status_code} on {path}: {response.text[:300]}",
                status_code=response.status_code,
            )
        return response.json()

    def list_messages(
        self, query: Optional[str] = None, page_token: Optional[str] = None
    ) -> dict:
        """One page of messages.list (newest-first).

        Note: SPAM and TRASH are excluded unless the query asks for them,
        which matches the desired "entire mailbox" semantics.
        """
        params: dict = {"maxResults": _LIST_PAGE_SIZE}
        if query:
            params["q"] = query
        if page_token:
            params["pageToken"] = page_token
        return self._get("/messages", params)

    def get_message(self, message_id: str) -> dict:
        """Full message resource (payload, headers, parts, internalDate)."""
        return self._get(f"/messages/{message_id}", {"format": "full"})

    def get_attachment(self, message_id: str, attachment_id: str) -> dict:
        """Attachment resource — `data` (base64url) is a TOP-LEVEL field."""
        return self._get(f"/messages/{message_id}/attachments/{attachment_id}")

    def get_profile(self) -> dict:
        """Mailbox profile: emailAddress, messagesTotal, historyId, ..."""
        return self._get("/profile")

    def history_list(
        self,
        start_history_id: str,
        page_token: Optional[str] = None,
    ) -> dict:
        """Change records since a historyId.

        Returns {history: [{messagesAdded|messagesDeleted|labelsAdded|
        labelsRemoved: [{message: {id, ...}}]}], nextPageToken?, historyId}.
        A 404 (GmailApiError.status_code == 404) means the start historyId
        is too old — history expired and a full resync is required.
        """
        params: dict = {"startHistoryId": str(start_history_id)}
        if page_token:
            params["pageToken"] = page_token
        return self._get("/history", params)

    def iter_message_ids(self, query: Optional[str] = None, max_pages: int = _ENUM_PAGES) -> list[str]:
        """Enumerate ALL message ids for a query, newest-first, up to max_pages.

        Raises GmailApiError if the window is larger than the page budget,
        rather than silently returning a truncated (wrong) "oldest" set.
        """
        ids: list[str] = []
        page_token: Optional[str] = None
        for _ in range(max_pages):
            page = self.list_messages(query, page_token)
            ids.extend(m["id"] for m in page.get("messages", []))
            page_token = page.get("nextPageToken")
            if not page_token:
                return ids
        raise GmailApiError(
            f"Query {query!r} has more than {max_pages * _LIST_PAGE_SIZE} messages; "
            "refusing to enumerate a window this large (would return a wrong "
            "'oldest' set)."
        )


def _date_query(upper_bound_ms: int) -> str:
    """Gmail `before:` query for everything strictly before a date (YYYY/MM/DD)."""
    return f"before:{time.strftime('%Y/%m/%d', time.gmtime(upper_bound_ms / 1000))}"


def _is_enumerable(api: GmailApiClient, query: Optional[str], max_pages: int = _ENUM_PAGES) -> bool:
    """Actually page through a query up to max_pages to check it's enumerable.

    Works against anything exposing list_messages() (real client or test fake).
    """
    page_token: Optional[str] = None
    for _ in range(max_pages):
        page = api.list_messages(query, page_token)
        page_token = page.get("nextPageToken")
        if not page_token:
            return True
    return False


def page_budget_for(cursor: int, limit: int) -> int:
    """List-page budget needed so enumeration reaches cursor + limit.

    Progressive ingestion raises its budget as the cursor advances; the
    ceiling (40 pages = 20,000 messages) is the point where this oldest-N
    strategy stops and historyId-based incremental sync must take over.
    """
    return min(40, max(_ENUM_PAGES, -(-(cursor + limit) // _LIST_PAGE_SIZE) + 2))


def find_oldest_message_ids(
    api: GmailApiClient,
    target: int = 10,
    verbose: bool = False,
    page_budget: int = _ENUM_PAGES,
) -> list[str]:
    """Find the ids of the `target` oldest messages in the mailbox.

    Gallop up from Gmail's launch date to bracket the largest `before:` date
    prefix that is ACTUALLY enumerable, binary search it to day granularity,
    then fully enumerate it. The oldest `target` messages are the head of
    that window in reverse order.

    page_budget bounds how deep enumeration may reach (in 500-message list
    pages). Progressive ingestion raises it as its cursor advances so the
    enumerable window always covers cursor + batch.

    Returns ids newest-first as the API yields them; callers reverse and
    slice to get the oldest `target`.
    """
    def log(msg: str) -> None:
        if verbose:
            print(f"[gmail search] {msg}")

    launch_ms = _GMAIL_LAUNCH_MS
    now_ms = int(time.time() * 1000)

    # Whole mailbox enumerable? Then no search is needed at all.
    if _is_enumerable(api, None, page_budget):
        log("whole mailbox within enumeration budget")
        return api.iter_message_ids(None, page_budget)

    # Gallop upward from launch: `lo` stays a known-enumerable prefix,
    # `hi` is the first prefix found NOT enumerable.
    lo_ms = launch_ms
    step_ms = 30 * _DAY_MS
    hi_ms = now_ms
    probe_ms = launch_ms + step_ms
    while probe_ms < now_ms:
        if _is_enumerable(api, _date_query(probe_ms), page_budget):
            lo_ms = probe_ms
            step_ms *= 2
            probe_ms = min(now_ms, probe_ms + step_ms)
        else:
            hi_ms = probe_ms
            break

    # Binary search the good/bad bracket to day granularity.
    while hi_ms - lo_ms > _DAY_MS:
        mid_ms = (lo_ms + hi_ms) // 2
        if _is_enumerable(api, _date_query(mid_ms), page_budget):
            lo_ms = mid_ms
        else:
            hi_ms = mid_ms
    log(f"candidate window: {_date_query(lo_ms)}")

    # Fully enumerate the window; widen by a day at a time if it holds
    # fewer than `target` messages (single days adding >6,000 messages
    # would fail enumeration — not a real-world case for a personal mailbox).
    ids: list[str] = []
    for _ in range(4):
        ids = api.iter_message_ids(_date_query(lo_ms), page_budget)
        log(f"window {_date_query(lo_ms)}: {len(ids)} messages")
        if len(ids) >= target or lo_ms <= launch_ms:
            return ids
        lo_ms = lo_ms + _DAY_MS
    return ids
