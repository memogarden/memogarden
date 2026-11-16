"""Polite HTTP fetching for feeds — the research doc §5 policy, in code.

Identification (§5.4): honest User-Agent with product/version, contact URL,
subscriber count 1, and a per-feed key so a publisher can correspond about
one specific feed. Accept header mirrors feedparser's shipped value.
Conditional GET (§5.2): validators are echoed VERBATIM (RFC 9110 §8.8.3 —
never parse-and-requote) and refreshed from every response, 304s included
(RFC 9111 §4.3.4 — a 304 may legitimately refresh validators).
"""

from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, UTC
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import requests

# Product/version + contact URL + honest subscriber count (Feedfetcher /
# Feedbin precedent; feedparser ships NO UA by default and asks embedders
# to set one). The per-feed key is appended per request.
BASE_USER_AGENT = (
    "MemoGarden-RSS/0.1 (+https://memogarden.ngjunsiang.me/privacy/;"
    " 1 subscriber)"
)

# feedparser's shipped Accept value (feedparser/http.py)
ACCEPT_HEADER = (
    "application/atom+xml, application/rdf+xml, application/rss+xml,"
    " application/xml;q=0.9, text/xml;q=0.2, */*;q=0.1"
)

_REDIRECT_LIMIT = 10  # requests' own default; we only count the hops


@dataclass
class FetchResult:
    """Outcome of one HTTP fetch. `body` is the DECOMPRESSED content
    (requests already handled Content-Encoding) — validators come from the
    headers exactly as the server sent them. Transport failures (DNS,
    refused, timeout, TLS) leave status_code=None and fill `error`; HTTP
    status codes are outcomes, not exceptions."""

    status_code: int | None = None
    body: bytes = b""
    etag: str | None = None
    last_modified: str | None = None
    final_url: str | None = None
    redirect_hops: int = 0
    retry_after_s: float | None = None
    headers: dict = field(default_factory=dict)
    error: str | None = None


def user_agent(feed_key: str) -> str:
    """Per-request UA with the feed tag (rachelbythebay's tagged-feed
    mechanism: publishers can report abuse against one specific feed)."""
    return f"{BASE_USER_AGENT}; feed-key: {feed_key}"


def feed_key_for(xml_url: str) -> str:
    """First 8 hex of SHA-256 of the feed URL — stable per subscription."""
    return hashlib.sha256(xml_url.encode("utf-8")).hexdigest()[:8]


def fetch(
    url: str,
    state: dict,
    feed_key: str,
    timeout: float = 15.0,
) -> FetchResult:
    """One conditional-GET request. Sends stored validators verbatim; never
    invents them (only echo what a previous response actually provided)."""
    headers = {
        "User-Agent": user_agent(feed_key),
        "Accept": ACCEPT_HEADER,
        # requests negotiates and decompresses gzip/deflate automatically
        "Accept-Encoding": "gzip, deflate",
    }
    if state.get("etag"):
        headers["If-None-Match"] = state["etag"]
    if state.get("last_modified"):
        # If-None-Match wins per RFC 9110 §13.1.3; sending both is the
        # RFC 9111 §4.3.1 SHOULD when both validators are held.
        headers["If-Modified-Since"] = state["last_modified"]

    try:
        resp = requests.get(
            url, headers=headers, timeout=timeout, allow_redirects=True, stream=True
        )
    except requests.RequestException as e:
        return FetchResult(error=f"{type(e).__name__}: {e}")

    with resp:
        result = FetchResult(
            status_code=resp.status_code,
            etag=resp.headers.get("ETag"),
            last_modified=resp.headers.get("Last-Modified"),
            final_url=resp.url,
            redirect_hops=len(resp.history),
            retry_after_s=parse_retry_after(resp.headers.get("Retry-After")),
            headers=dict(resp.headers),
        )
        if resp.status_code == 200:
            result.body = resp.content
        return result


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Retry-After per RFC 9110 §10.2.3: delay-seconds OR HTTP-date."""
    if not value:
        return None
    value = value.strip()
    if re.fullmatch(r"\d+", value):
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    now = now or datetime.now(UTC)
    if when.tzinfo is None:
        # RFC 7231 required GMT; treat naive as UTC
        when = when.replace(tzinfo=UTC)
    delta = (when - now).total_seconds()
    return max(0.0, delta)


def apply_jitter(delay_s: float) -> float:
    """Full jitter (AWS/Brooker): uniform over half the computed delay so
    retries never synchronize into clusters."""
    return random.uniform(0.5, 1.0) * max(0.0, delay_s)


def error_backoff_s(
    status_code: int | None,
    consecutive_errors: int,
    retry_after_s: float | None,
    base_interval_s: float,
    max_backoff_s: float,
) -> float:
    """Backoff multiplier per the research doc §5.3 table. `consecutive_errors`
    counts THIS failure (1 = first). Returns the raw delay (un-jittered);
    the caller applies jitter and caps."""
    # 429: honor Retry-After; floor 60 min (NewsBlur precedent); escalate ×2
    if status_code == 429:
        delay = retry_after_s if retry_after_s is not None else 3600.0
        return max(delay, 3600.0) * (2 ** (consecutive_errors - 1))
    # 503: honor Retry-After, else ×2
    if status_code == 503:
        if retry_after_s is not None:
            return retry_after_s
        return base_interval_s * (2 ** min(consecutive_errors, 6))
    # 404: ×4 immediately (streak → tombstone is the provider's call)
    if status_code == 404:
        return base_interval_s * (4 ** min(consecutive_errors, 4))
    # other 4xx (403 walls etc.), other 5xx, transport: ×2
    return base_interval_s * (2 ** min(consecutive_errors, 6))


def tombstone_dead(
    status_code: int | None,
    consecutive_errors: int,
    first_error_at: str | None,
    now_iso: str,
) -> bool:
    """404 streak → tombstone: 20 consecutive, or 30 days continuously
    failing (RFC 9110 §15.5.5: 404 alone doesn't say permanent; the streak
    is the heuristic). Non-404 errors never feed this — "site down" is not
    "feed gone"."""
    if status_code != 404:
        return False
    if consecutive_errors >= 20:
        return True
    if first_error_at:
        try:
            span = (
                datetime.fromisoformat(now_iso)
                - datetime.fromisoformat(first_error_at)
            ).total_seconds()
            return span >= 30 * 86400
        except ValueError:
            return False
    return False


# ---------------------------------------------------------------------------
# Feed-declared schedule hints — honored as floors, never as mandates
# (RSS Board Profile: treat <ttl> "as a suggestion, not a hard rule"; <1% of
# feeds publish skipHours). Best-effort scans over the raw XML so they work
# wherever feedparser doesn't surface the element; a bozo feed simply
# yields no hints.
# ---------------------------------------------------------------------------


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def parse_hints(raw_body: bytes) -> dict:
    """Best-effort {ttl_minutes, skip_hours, sy_period_s} from the raw feed
    XML. Tolerant: any parse failure returns whatever was found ({})."""
    hints: dict = {}
    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(raw_body)
    except Exception:  # noqa: BLE001 — hints are best-effort by design
        return hints

    for elem in root.iter():
        name = _local_name(elem.tag)
        if name == "ttl" and elem.text and not hints.get("ttl_minutes"):
            try:
                hints["ttl_minutes"] = max(0, int(elem.text.strip()))
            except ValueError:
                pass
        elif name == "hour" and elem.text:
            try:
                hour = int(elem.text.strip())
            except ValueError:
                continue
            # RSS 0.91 legacy + RSS Board Profile: 0 and 24 are both midnight
            if hour == 24:
                hour = 0
            if 0 <= hour <= 23:
                hints.setdefault("skip_hours", set()).add(hour)
        elif name == "updatePeriod" and elem.text:
            hints["sy_period"] = elem.text.strip().lower()
        elif name == "updateFrequency" and elem.text:
            try:
                hints["sy_frequency"] = max(1, int(elem.text.strip()))
            except ValueError:
                pass

    if hints.get("sy_period"):
        seconds = {
            "hourly": 3600,
            "daily": 86400,
            "weekly": 7 * 86400,
            "monthly": 30 * 86400,
            "yearly": 365 * 86400,
        }.get(hints["sy_period"])
        if seconds:
            freq = hints.get("sy_frequency", 1)
            hints["sy_period_s"] = max(3600, seconds // freq)
    return hints


def parse_max_age(headers: dict) -> int | None:
    """Cache-Control max-age from the last 200 — a floor, capped by the
    caller (RFC 9111 §5.2.2.1: staleness guidance, not a polling mandate)."""
    value = headers.get("Cache-Control") or headers.get("cache-control")
    if not value:
        return None
    match = re.search(r"max-age\s*=\s*(\d+)", value, re.IGNORECASE)
    return int(match.group(1)) if match else None


def skip_hours_hit(skip_hours: set[int] | None, now_utc: datetime) -> bool:
    """True when the feed's <skipHours> (hours in GMT per the RSS 2.0 spec)
    covers the current UTC hour."""
    return bool(skip_hours) and now_utc.hour in skip_hours


def host_of(url: str) -> str:
    return urlsplit(url).hostname or ""
