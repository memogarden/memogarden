"""Wire-protocol and politeness-math tests — the research doc §5 policy,
verified against a real localhost HTTP server."""

import time
from datetime import UTC, datetime, timedelta

from rss_importer import fetcher

RSS_OK = (
    "<?xml version=\"1.0\"?><rss version=\"2.0\"><channel>"
    "<title>T</title><link>http://t.example/</link><description>d</description>"
    "</channel></rss>"
).encode()


def test_user_agent_identifies_honestly(feed_server):
    """UA carries product/version, contact URL, subscriber count, feed-key
    (research doc §5.4 — never omit, never fake a browser)."""
    url = feed_server.set("/f.xml", status=200, body=RSS_OK)
    fetcher.fetch(url, {}, "3f9a1c2b", timeout=5)
    req = feed_server.requests[-1]
    ua = req["headers"]["user-agent"]
    assert ua.startswith("MemoGarden-RSS/0.1 (+https://memogarden.ngjunsiang.me")
    assert "1 subscriber" in ua
    assert ua.endswith("feed-key: 3f9a1c2b")
    # feedparser-style Accept (§5.4)
    assert "application/atom+xml" in req["headers"]["accept"]
    assert "gzip" in req["headers"]["accept-encoding"]


def test_conditional_get_echoes_verbatim_validators(feed_server):
    """Validators echo byte-for-byte, incl. the W/ weak prefix and quotes
    (RFC 9110 §8.8.3) — never parse-and-requote."""
    url = feed_server.set(
        "/f.xml", status=200, body=RSS_OK, etag='W/"abc-42"',
        headers={"ETag": 'W/"abc-42"', "Last-Modified": "Mon, 02 Jan 2006 15:04:05 GMT"},
    )
    state = {"etag": 'W/"abc-42"', "last_modified": "Mon, 02 Jan 2006 15:04:05 GMT"}
    fetcher.fetch(url, state, "k1", timeout=5)
    req = feed_server.requests[-1]
    assert req["headers"]["if-none-match"] == 'W/"abc-42"'
    assert req["headers"]["if-modified-since"] == "Mon, 02 Jan 2006 15:04:05 GMT"


def test_304_when_validator_matches(feed_server):
    """Conditional-GET simulation: matching validator → 304, empty body."""
    url = feed_server.set(
        "/f.xml", status=200, body=RSS_OK, etag='"v1"',
        headers={"ETag": '"v1"'},
    )
    result = fetcher.fetch(url, {}, "k1", timeout=5)
    assert result.status_code == 200 and result.etag == '"v1"'
    result = fetcher.fetch(url, {"etag": '"v1"'}, "k1", timeout=5)
    assert result.status_code == 304
    assert result.body == b""
    assert result.etag == '"v1"'  # a 304 still refreshes validators (§4.3.4)


def test_never_invents_validators(feed_server):
    """A fresh state sends NO conditional headers (the NextCloud-News
    anti-pattern: fabricated If-Modified-Since values)."""
    url = feed_server.set("/f.xml", status=200, body=RSS_OK)
    fetcher.fetch(url, {}, "k1", timeout=5)
    req = feed_server.requests[-1]
    assert "if-none-match" not in req["headers"]
    assert "if-modified-since" not in req["headers"]


def test_redirects_followed_and_measured(feed_server):
    feed_server.set("/moved.xml", status=200, body=RSS_OK)
    feed_server.httpd.specs["/old.xml"] = {
        "status": 301,
        "headers": {"Location": feed_server.url("/moved.xml")},
        "body": b"",
    }
    result = fetcher.fetch(feed_server.url("/old.xml"), {}, "k1", timeout=5)
    assert result.status_code == 200
    assert result.final_url == feed_server.url("/moved.xml")
    assert result.redirect_hops == 1


def test_transport_failure_is_reported_not_raised():
    # port 9 (symbiont/discarded service) is refused locally; refused is
    # immediate, so this stays fast
    result = fetcher.fetch("http://127.0.0.1:9/f.xml", {}, "k1", timeout=2)
    assert result.error is not None
    assert result.status_code is None


# ---------------------------------------------------------------------------
# Backoff table (§5.3)
# ---------------------------------------------------------------------------


def test_backoff_429_floors_at_60min_and_escalates():
    assert fetcher.error_backoff_s(429, 1, None, 3600, 604800) == 3600
    # Retry-After honored when larger than the floor
    assert fetcher.error_backoff_s(429, 1, 86400.0, 3600, 604800) == 86400
    # escalation ×2 per subsequent 429 (resetting backoff is the sin
    # rachelbythebay documented)
    assert fetcher.error_backoff_s(429, 2, None, 3600, 604800) == 7200


def test_backoff_503_honors_retry_after_else_doubles():
    assert fetcher.error_backoff_s(503, 1, 120.0, 3600, 604800) == 120
    assert fetcher.error_backoff_s(503, 1, None, 3600, 604800) == 7200


def test_backoff_404_quadruples():
    assert fetcher.error_backoff_s(404, 1, None, 3600, 604800) == 14400


def test_backoff_other_errors_double():
    assert fetcher.error_backoff_s(403, 1, None, 3600, 604800) == 7200
    assert fetcher.error_backoff_s(None, 2, None, 3600, 604800) == 14400  # transport


def test_apply_jitter_stays_in_full_jitter_bounds():
    value = fetcher.apply_jitter(1000.0)
    assert 500.0 <= value <= 1000.0


# ---------------------------------------------------------------------------
# 404-streak tombstone (§5.3)
# ---------------------------------------------------------------------------


def test_tombstone_after_20_consecutive_404s():
    assert not fetcher.tombstone_dead(404, 19, None, "2026-11-16T00:00:00+00:00")
    assert fetcher.tombstone_dead(404, 20, None, "2026-11-16T00:00:00+00:00")


def test_tombstone_after_30_days_of_404s():
    start = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    assert fetcher.tombstone_dead(404, 5, start, datetime.now(UTC).isoformat())


def test_non_404_never_tombstones():
    """'Site down' is not 'feed gone' — transport/5xx/403 never feed the
    death streak."""
    start = (datetime.now(UTC) - timedelta(days=90)).isoformat()
    assert not fetcher.tombstone_dead(503, 50, start, datetime.now(UTC).isoformat())
    assert not fetcher.tombstone_dead(None, 50, start, datetime.now(UTC).isoformat())
    assert not fetcher.tombstone_dead(403, 50, start, datetime.now(UTC).isoformat())


# ---------------------------------------------------------------------------
# Retry-After (RFC 9110 §10.2.3: delay-seconds OR HTTP-date)
# ---------------------------------------------------------------------------


def test_retry_after_seconds_and_http_date():
    assert fetcher.parse_retry_after("120") == 120.0
    future = datetime.now(UTC) + timedelta(seconds=90)
    as_date = future.strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert 80 <= fetcher.parse_retry_after(as_date) <= 90
    assert fetcher.parse_retry_after(None) is None
    assert fetcher.parse_retry_after("garbage") is None


# ---------------------------------------------------------------------------
# Feed-declared hints (§5.1 — floors, never mandates)
# ---------------------------------------------------------------------------


def test_parse_hints_ttl_skiphours_sy_module():
    body = (
        "<?xml version=\"1.0\"?>"
        "<rss version=\"2.0\" xmlns:sy=\"http://purl.org/rss/1.0/modules/syndication/\">"
        "<channel>"
        "<title>T</title><link>http://t.example/</link><description>d</description>"
        "<ttl>90</ttl>"
        "<skipHours><hour>3</hour><hour>4</hour><hour>24</hour></skipHours>"
        "<sy:updatePeriod>daily</sy:updatePeriod>"
        "<sy:updateFrequency>2</sy:updateFrequency>"
        "</channel></rss>"
    ).encode()
    hints = fetcher.parse_hints(body)
    assert hints["ttl_minutes"] == 90
    assert hints["skip_hours"] == {3, 4, 0}  # 24 is midnight per the profile
    assert hints["sy_period_s"] == max(3600, 86400 // 2)


def test_parse_hints_tolerates_garbage():
    assert fetcher.parse_hints(b"not xml at all") == {}


def test_parse_max_age():
    assert fetcher.parse_max_age({"Cache-Control": "public, max-age=7200"}) == 7200
    assert fetcher.parse_max_age({"Cache-Control": "no-cache"}) is None
    assert fetcher.parse_max_age({}) is None
