"""Event drain (register C3/C4): §12.1 events mirror as SessionEvent
facts, page_visited as PageVisit facts, download_captured filtered,
legacy duplicate-seq files exact via (session, seq, ts) probes."""

import json

from conftest import _now  # noqa: F401  (fixture helpers live there)


def _append_event(sdir, **rec):
    with (sdir / "events.jsonl").open("a") as f:
        f.write(json.dumps(rec) + "\n")


def test_control_event_mirrors_as_session_event(provider, make_session, drain):
    sdir = make_session("dbs")
    _append_event(sdir, ts=1.0, seq=1, event="agent_request", tab=2,
                  label="estatements", actor="agent", to="requested")

    facts = drain()
    assert len(facts) == 1
    fact = facts[0]
    assert fact._type == "SessionEvent"
    assert fact.data["event"] == "agent_request"       # discriminator
    assert fact.data["actor"] == "agent"
    assert fact.metadata["stream"] == "events"
    assert fact.metadata["session"] == "dbs"
    assert fact.metadata["provider"] == "memogarden-sessions"


def test_page_visit_mirrors_as_page_visit(provider, make_session, drain):
    sdir = make_session("uob")
    _append_event(sdir, ts=2.5, seq=2, event="page_visited",
                  url="https://pib.uob.com.sg/estatements",
                  title="UOB | eStatements", visited_at="2026-11-19T10:00:00",
                  actor="operator", session="uob", machine="laptop")

    facts = drain()
    assert len(facts) == 1
    fact = facts[0]
    assert fact._type == "PageVisit"
    assert fact.data["url"] == "https://pib.uob.com.sg/estatements"
    assert fact.data["title"] == "UOB | eStatements"
    assert fact.metadata["stream"] == "history"
    assert fact.metadata["account"] == "uob"


def test_download_captured_filtered(provider, make_session, drain):
    """D2: folded into Attachment metadata, never its own fact."""
    sdir = make_session("dbs")
    _append_event(sdir, ts=3.0, seq=3, event="download_captured",
                  filename="statement.pdf", size=100, actor="operator")

    facts = drain()
    assert facts == []


def test_legacy_duplicate_seq_both_drain(provider, make_session, drain):
    """Pre-seq-persistence files: seq resets across daemon restarts. Both
    runs' events must drain (ts disambiguates), and the rerun drains none."""
    sdir = make_session("dbs")
    # run 1 (legacy): seq 1..2
    _append_event(sdir, ts=100.0, seq=1, event="session_created", actor="daemon")
    _append_event(sdir, ts=101.0, seq=2, event="session_opened", actor="daemon")
    # run 2 (legacy restart): seq RESET — 1 again, different ts
    _append_event(sdir, ts=200.0, seq=1, event="session_dehydrated", actor="daemon")

    facts = drain()
    assert len(facts) == 3
    events = sorted(f.data["ts"] for f in facts)
    assert events == [100.0, 101.0, 200.0]

    assert drain() == []          # rerun: all probed as drained


def test_malformed_lines_skipped(provider, make_session, drain):
    sdir = make_session("dbs")
    with (sdir / "events.jsonl").open("a") as f:
        f.write("{not json\n")
        f.write(json.dumps({"event": "agent_request"}) + "\n")  # no seq/ts
    _append_event(sdir, ts=9.0, seq=9, event="operator_grant", tab=1, actor="operator")

    facts = drain()
    assert len(facts) == 1
    assert facts[0].data["event"] == "operator_grant"


def test_mixed_drain_files_and_events(provider, make_session, drain):
    """One generator covers both: staged file drains, then the event."""
    sdir = make_session("uob")
    (sdir / "downloads").mkdir()
    (sdir / "downloads" / "a.pdf").write_bytes(b"%PDF-1.5 x")
    _append_event(sdir, ts=5.0, seq=5, event="session_created", actor="daemon")

    facts = drain()
    kinds = sorted(f._type for f in facts)
    assert kinds == ["Attachment", "SessionEvent"]
