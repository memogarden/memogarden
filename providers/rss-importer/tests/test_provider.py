"""Sweep-behavior tests: done-sets, revisions, archiving, backoff, circuit
breaker, budget, and the politeness state machine — against a real localhost
HTTP server and a real tmp Soil."""

from datetime import UTC, datetime, timedelta

import pytest

from conftest import (
    force_due,
    make_provider,
    make_subscription,
    rss_body,
    rss_item,
    run_sync,
)
from rss_importer.state import new_feed_state

FEED = "/feed.xml"


def due_now_only(state):
    """Force a feed due right now (streak tests drive _sync_feed directly)."""
    state["next_due_at"] = None


# ---------------------------------------------------------------------------
# Happy path: first sweep archives + ingests; second sweep is a 304
# ---------------------------------------------------------------------------


def test_first_sweep_archives_and_ingests(soil, state_path, feed_server, artifacts_dir):
    url = feed_server.set(
        FEED, status=200, etag="etag-1", headers={"ETag": "etag-1"},
        body=rss_body(
            rss_item(),
            rss_item(guid="urn:2", title="I2", link="http://test.example/2"),
        ),
    )
    make_subscription(soil, url)
    provider = make_provider(soil, state_path, artifacts_dir=artifacts_dir)

    facts = run_sync(provider, soil)
    types = [f._type for f in facts]
    # one archived response + two entries (decision §8.2: provenance where
    # provenance is due)
    assert types.count("Entry") == 2
    assert types.count("Attachment") == 1
    attachment = next(f for f in facts if f._type == "Attachment")
    assert attachment.metadata["artifact"] == "feed-response"
    # the archived bytes are verbatim
    with open(attachment.data["content_path"], "rb") as fh:
        assert fh.read() == feed_server.httpd.specs[FEED]["body"]

    # state advanced: validators stored verbatim, schedule set
    states = provider._states.load()
    assert states[url]["etag"] == "etag-1"
    assert states[url]["next_due_at"] is not None

    # second sweep gets a 304 (server sees the echoed etag) → nothing new
    force_due(provider, url)
    facts2 = run_sync(provider, soil)
    assert facts2 == []
    last = feed_server.requests[-1]
    assert last["headers"].get("if-none-match") == "etag-1"


def test_repeat_200_with_identical_body_parses_nothing(soil, state_path, feed_server, artifacts_dir):
    """No-validator feed, same body twice: the body-hash skip prevents
    re-ingest (the no-validator fallback, §5.2)."""
    url = feed_server.set(FEED, status=200, body=rss_body(rss_item()))
    make_subscription(soil, url)
    provider = make_provider(soil, state_path, artifacts_dir=artifacts_dir)
    assert len(run_sync(provider, soil)) == 2  # archive + entry
    force_due(provider, url)
    assert len(run_sync(provider, soil)) == 0  # unchanged body → silent skip


# ---------------------------------------------------------------------------
# Revision flow (§8.3)
# ---------------------------------------------------------------------------


def test_publisher_edit_yields_one_revision_note(soil, state_path, feed_server, artifacts_dir):
    url = feed_server.set(FEED, status=200, body=rss_body(rss_item()))
    make_subscription(soil, url)
    provider = make_provider(soil, state_path, artifacts_dir=artifacts_dir)
    run_sync(provider, soil)

    edited = rss_item(
        pubdate="Sat, 07 Jan 2006 15:04:05 GMT",
        description="<p>one (corrected)</p>",
    )
    feed_server.set(FEED, status=200, body=rss_body(edited))
    force_due(provider, url)
    facts = run_sync(provider, soil)
    notes = [f for f in facts if f._type == "Note"]
    assert len(notes) == 1
    assert notes[0].data["description"] == "<p>one (corrected)</p>"
    assert notes[0].metadata["revision"] is True

    # the same revision is never re-recorded (done-set on entry_id+revised_at)
    assert run_sync(provider, soil) == []


def test_timestamp_only_touch_yields_nothing(soil, state_path, feed_server, artifacts_dir):
    """`updated` bumped but content identical → churn nothing (feeds that
    bump `updated` on republish)."""
    url = feed_server.set(FEED, status=200, body=rss_body(rss_item()))
    make_subscription(soil, url)
    provider = make_provider(soil, state_path, artifacts_dir=artifacts_dir)
    run_sync(provider, soil)
    # same description, different guid is a NEW entry; same guid + same
    # content + newer timestamp is the touch case — simulate via stored index:
    touched = rss_item(pubdate="Tue, 03 Jan 2006 15:04:05 GMT")
    feed_server.set(FEED, status=200, body=rss_body(touched))
    force_due(provider, url)
    # the item's stored updated is 2006-01-02; feed now says 2006-01-03 but
    # the CONTENT hash is identical → no revision
    assert run_sync(provider, soil) == []


# ---------------------------------------------------------------------------
# Full-article fetches (§4.5 — opt-in, one page per new entry, fetched once)
# ---------------------------------------------------------------------------


def test_full_fetch_opt_in_archives_article_pages(soil, state_path, feed_server, artifacts_dir):
    article_url = feed_server.set("/1", status=200, body=b"<html><body>full article</body></html>")
    url = feed_server.set(FEED, status=200, body=rss_body(rss_item(link=article_url)))
    make_subscription(soil, url)
    article = feed_server.set(
        "/1", status=200, body=b"<html><body>full article</body></html>"
    )
    provider = make_provider(soil, state_path, artifacts_dir=artifacts_dir)
    # opt in via the state store (operator action)
    states = provider._states.load()
    states.setdefault(url, new_feed_state())["full_fetch"] = True
    provider._states.save(states)

    facts = run_sync(provider, soil)
    articles = [f for f in facts if f._type == "Attachment" and f.metadata["artifact"] == "article"]
    assert len(articles) == 1
    assert articles[0].metadata["entry_id"] == "urn:1"

    # default OFF: a fresh non-opted-in feed archives no articles
    url2 = feed_server.set("/feed2.xml", status=200, body=rss_body(rss_item(guid="urn:9", link="http://test.example/9", title="I9")))
    make_subscription(soil, url2)
    provider2 = make_provider(soil, state_path, artifacts_dir=artifacts_dir)
    facts2 = run_sync(provider2, soil)
    assert not any(
        f._type == "Attachment" and f.metadata.get("artifact") == "article"
        for f in facts2
    )


# ---------------------------------------------------------------------------
# Error classes (§5.3)
# ---------------------------------------------------------------------------


def test_410_gone_is_terminal(soil, state_path, feed_server, artifacts_dir):
    url = feed_server.set(FEED, status=410)
    make_subscription(soil, url)
    provider = make_provider(soil, state_path, artifacts_dir=artifacts_dir)
    assert run_sync(provider, soil) == []
    states = provider._states.load()
    assert states[url]["status"] == "gone"
    # never polled again — not even due
    feed_server.set(FEED, status=200, body=rss_body(rss_item()))
    assert run_sync(provider, soil) == []
    requests_before = len(feed_server.requests)
    provider.sync()  # consume without storing
    assert len(feed_server.requests) == requests_before


def test_404_streak_tombstones_after_20(soil, state_path, feed_server, artifacts_dir):
    url = feed_server.set(FEED, status=404)
    make_subscription(soil, url)
    provider = make_provider(soil, state_path, artifacts_dir=artifacts_dir)
    states = provider._states.load()
    state = states.setdefault(url, new_feed_state())
    done, revisions, stored = set(), set(), {}
    for i in range(1, 21):
        list(provider._sync_feed(url, state, done, revisions, stored, set()))
    assert state["status"] == "dead"
    # dead feeds probe weekly, not hourly
    from rss_importer.provider import _DEAD_PROBE_S
    next_due = datetime.fromisoformat(state["next_due_at"])
    assert next_due > datetime.now(UTC) + timedelta(seconds=_DEAD_PROBE_S - 120)


def test_429_retry_after_honored(soil, state_path, feed_server, artifacts_dir):
    url = feed_server.set(FEED, status=429, headers={"Retry-After": "86400"})
    make_subscription(soil, url)
    provider = make_provider(soil, state_path, artifacts_dir=artifacts_dir)
    states = provider._states.load()
    state = states.setdefault(url, new_feed_state())
    list(provider._sync_feed(url, state, set(), set(), {}, set()))
    # Retry-After: 86400 honored, not collapsed into a default backoff
    next_due = datetime.fromisoformat(state["next_due_at"])
    assert next_due >= datetime.now(UTC) + timedelta(seconds=86400 * 0.5 - 120)


def test_transport_failure_no_tombstone(soil, state_path, artifacts_dir):
    """A dead host never kills its feeds — 'site down' is not 'feed gone'."""
    url = "http://127.0.0.1:9/feed.xml"
    make_subscription(soil, url)
    provider = make_provider(soil, state_path, artifacts_dir=artifacts_dir)
    states = provider._states.load()
    state = states.setdefault(url, new_feed_state())
    for _ in range(30):
        list(provider._sync_feed(url, state, set(), set(), {}, set()))
    assert state["status"] == "active"
    assert state["consecutive_errors"] == 30


def test_parse_error_streak_pauses(soil, state_path, feed_server, artifacts_dir):
    url = feed_server.set(FEED, status=200, body=b"this is not xml <at all")
    make_subscription(soil, url)
    provider = make_provider(soil, state_path, artifacts_dir=artifacts_dir)
    states = provider._states.load()
    state = states.setdefault(url, new_feed_state())
    for _ in range(3):
        list(provider._sync_feed(url, state, set(), set(), {}, set()))
    assert state["status"] == "paused"
    assert state["parse_errors"] == 3
    # paused feeds are skipped by sweeps
    assert provider._due_feeds([url], states, datetime.now(UTC).isoformat()) == []


# ---------------------------------------------------------------------------
# Sweep mechanics: breaker, budget, politeness, adaptive interval
# ---------------------------------------------------------------------------


def test_host_circuit_breaker(soil, state_path, feed_server, monkeypatch, artifacts_dir):
    """When one feed's host transport-fails, the host's other feeds are
    deferred this sweep — without counting failures against them."""
    dead = "http://127.0.0.1:9/a.xml"
    alive = feed_server.set(FEED, status=200, body=rss_body(rss_item(guid="urn:h")))
    make_subscription(soil, dead)
    make_subscription(soil, alive)
    # deterministic order: dead feed first
    monkeypatch.setattr("rss_importer.provider.random.shuffle", lambda lst: None)
    provider = make_provider(soil, state_path, artifacts_dir=artifacts_dir)
    facts = run_sync(provider, soil)
    # dead feed yields nothing; alive feed must NOT have been contacted
    assert facts == []
    assert all(r["path"] != FEED for r in feed_server.requests)


def test_budget_defers_remaining_feeds(soil, state_path, feed_server, artifacts_dir):
    url1 = feed_server.set("/f1.xml", status=200, body=rss_body(rss_item(guid="urn:a", link="http://test.example/a")))
    url2 = feed_server.set("/f2.xml", status=200, body=rss_body(rss_item(guid="urn:b", link="http://test.example/b")))
    make_subscription(soil, url1)
    make_subscription(soil, url2)
    # budget 0 → nothing polled, nothing failed
    provider = make_provider(
        soil, state_path, sweep_budget_s=-1, artifacts_dir=artifacts_dir
    )
    assert run_sync(provider, soil) == []
    assert feed_server.requests == []


def test_inter_request_pacing(soil, state_path, feed_server, monkeypatch, artifacts_dir):
    """The politeness floor between requests is honored (§5.1 — never burst)."""
    url = feed_server.set(FEED, status=200, body=rss_body(rss_item()))
    make_subscription(soil, url)
    provider = make_provider(
        soil, state_path, inter_request_sleep=0.05, artifacts_dir=artifacts_dir
    )
    sleeps = []
    monkeypatch.setattr(
        "rss_importer.provider.time.sleep", lambda s: sleeps.append(s)
    )
    run_sync(provider, soil)
    assert sleeps and all(s >= 0.05 for s in sleeps)


def test_adaptive_interval_grows_for_slow_feeds(soil, state_path, feed_server, artifacts_dir):
    """Blending lets intervals grow toward observed cadence (§5.1) — a
    monthly blog ends up polled daily, not hourly."""
    url = feed_server.set(FEED, status=200, body=rss_body(rss_item()))
    make_subscription(soil, url)
    provider = make_provider(soil, state_path, artifacts_dir=artifacts_dir)
    states = provider._states.load()
    state = states.setdefault(url, new_feed_state())
    now = datetime.now(UTC)
    state["interval_s"] = 3600
    state["changes_count"] = 3
    state["last_change_at"] = (now - timedelta(days=40)).isoformat()
    provider._adapt_interval(state, now)
    assert state["interval_s"] == 86400  # clamped at the 24h ceiling


def test_ttl_hint_raises_interval_floor(soil, state_path, feed_server, artifacts_dir):
    """<ttl> 90 min is honored as a FLOOR (never as a mandate below our 1h
    base; never past the 24h cap)."""
    url = feed_server.set(FEED, status=200, body=rss_body(rss_item(), ttl=90))
    make_subscription(soil, url)
    provider = make_provider(soil, state_path, artifacts_dir=artifacts_dir)
    run_sync(provider, soil)
    states = provider._states.load()
    assert states[url]["hints"]["ttl_minutes"] == 90
    next_due = datetime.fromisoformat(states[url]["next_due_at"])
    assert next_due >= datetime.now(UTC) + timedelta(minutes=45)  # jitter ≥ half


def test_redirect_target_adopted_identity_kept(soil, state_path, feed_server, artifacts_dir):
    """301 → fetch the target; after adoption fetch_url points at the
    target while the Subscription identity (xml_url) never moves (§7.4)."""
    target = feed_server.set("/new-home.xml", status=200, body=rss_body(rss_item(guid="urn:moved")))
    feed_server.httpd.specs["/old.xml"] = {
        "status": 301,
        "headers": {"Location": target},
        "body": b"",
    }
    old_url = feed_server.url("/old.xml")
    make_subscription(soil, old_url)
    provider = make_provider(soil, state_path, artifacts_dir=artifacts_dir)
    facts = run_sync(provider, soil)
    assert [f._type for f in facts].count("Entry") == 1
    states = provider._states.load()
    assert states[old_url]["fetch_url"] == target
    # next sweep fetches the adopted URL directly
    requests_before = len(feed_server.requests)
    run_sync(provider, soil)
    new_requests = feed_server.requests[requests_before:]
    assert all(r["path"] == "/new-home.xml" for r in new_requests)


# ---------------------------------------------------------------------------
# Relations (contained_by wiring, runtime-style)
# ---------------------------------------------------------------------------


def test_relations_wire_entries_and_artifacts(soil, state_path, feed_server, artifacts_dir):
    url = feed_server.set(FEED, status=200, body=rss_body(rss_item()))
    make_subscription(soil, url)
    provider = make_provider(soil, state_path, artifacts_dir=artifacts_dir)
    facts = run_sync(provider, soil)

    entries = [f for f in facts if f._type == "Entry"]
    attachments = [f for f in facts if f._type == "Attachment"]
    assert len(entries) == 1 and len(attachments) == 1

    # verify relations through the soil relation table
    with soil as s:
        rows = s._get_connection().execute(
            "SELECT kind, source, target FROM system_relation"
        ).fetchall()
    by_source = {r["source"]: r["target"] for r in rows}
    assert len(rows) == 2
    assert by_source[entries[0].uuid] is not None
    assert by_source[attachments[0].uuid] is not None


def test_per_feed_errors_do_not_fail_the_run(
    soil, state_path, feed_server, monkeypatch, artifacts_dir
):
    """One failing feed never fails the run (per-item error house pattern):
    HTTP-error feeds are absorbed; a transport-dead host trips the breaker
    but the run itself stays green."""
    url5 = feed_server.set("/f500.xml", status=500)
    make_subscription(soil, url5)
    url_ok = feed_server.set(
        "/fok.xml", status=200, body=rss_body(rss_item(guid="urn:ok"))
    )
    make_subscription(soil, url_ok)
    make_subscription(soil, "http://127.0.0.1:9/dead.xml")  # transport failure, last
    monkeypatch.setattr("rss_importer.provider.random.shuffle", lambda lst: None)
    provider = make_provider(soil, state_path, artifacts_dir=artifacts_dir)
    facts = run_sync(provider, soil)  # no exception
    types = [f._type for f in facts]
    assert "Entry" in types and "Attachment" in types


# ---------------------------------------------------------------------------
# Subscription enumeration
# ---------------------------------------------------------------------------


def test_no_subscriptions_is_a_clean_noop(soil, state_path, artifacts_dir):
    provider = make_provider(soil, state_path, artifacts_dir=artifacts_dir)
    assert run_sync(provider, soil) == []


def test_invalid_xml_urls_skipped(soil, state_path, artifacts_dir):
    make_subscription(soil, "not-a-url")
    make_subscription(soil, "ftp://nope.example/x")
    provider = make_provider(soil, state_path, artifacts_dir=artifacts_dir)
    assert run_sync(provider, soil) == []
