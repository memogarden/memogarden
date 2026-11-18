"""Drain behavior: staged file -> Attachment fact -> staged file deleted.
Register items exercised: D1 (gmail parity), D2 (provenance folded),
D3 (session,sha256 dedup), B3 (replay-safe), B6 (partials untouched),
F2 (zero local retention)."""

import json
import os
import time

PDF = b"%PDF-1.5 fake statement"


def _stage(sdir, name, data):
    d = sdir / "downloads"
    d.mkdir(exist_ok=True)
    (d / name).write_bytes(data)


def _capture_event(sdir, **fields):
    with (sdir / "events.jsonl").open("a") as f:
        f.write(json.dumps({"ts": 1.0, "seq": 1,
                            "event": "download_captured", **fields}) + "\n")


def test_drain_creates_fact_deletes_file(provider, make_session, drain):
    sdir = make_session("uob", machine="laptop")
    _capture_event(sdir, filename="statement.pdf",
                   url="https://pib.uob.com.sg/estatements/nov.pdf",
                   captured_at="2026-11-15T22:10:00",
                   downloaded_by="operator", content_type="application/pdf")
    _stage(sdir, "statement.pdf", PDF)

    facts = drain()

    assert len(facts) == 1
    fact = facts[0]
    assert fact.data["filename"] == "statement.pdf"
    assert fact.data["mime_type"] == "application/pdf"
    assert fact.data["size"] == len(PDF)
    assert fact.data["sha256"]
    assert fact.metadata["session"] == "uob"
    assert fact.metadata["account"] == "uob"
    assert fact.metadata["machine"] == "laptop"
    assert fact.metadata["stream"] == "downloads"
    assert fact.metadata["source"] == "browser_session"
    assert fact.metadata["url"].startswith("https://pib.uob.com.sg")
    assert fact.metadata["downloaded_by"] == "operator"
    assert fact.metadata["captured_at"] == "2026-11-15T22:10:00"

    # cache semantics: staged file gone, bytes live in the store
    assert not (sdir / "downloads" / "statement.pdf").exists()
    stored = provider.store_root / fact.data["content_path"]
    assert stored.read_bytes() == PDF


def test_replay_is_idempotent(provider, make_session, drain):
    """B2/B3: a fact committed but its staged file still present (crash
    window) must not duplicate on the next run."""
    sdir = make_session("dbs")
    _stage(sdir, "stmt.pdf", PDF)
    facts1 = drain()
    assert len(facts1) == 1
    assert not (sdir / "downloads" / "stmt.pdf").exists()

    # simulate the crash window: file reappears (redownload) with same bytes
    _stage(sdir, "stmt.pdf", PDF)
    facts2 = drain()
    assert facts2 == []
    assert not (sdir / "downloads" / "stmt.pdf").exists()


def test_partial_files_untouched(provider, make_session, drain):
    sdir = make_session("dbs")
    _stage(sdir, "big.pdf.crdownload", b"half")
    assert drain() == []
    assert (sdir / "downloads" / "big.pdf.crdownload").exists()


def test_screenshots_drain(provider, make_session, drain):
    sdir = make_session("proj")
    shot = sdir / "screenshots"
    shot.mkdir()
    (shot / "shot-120000.png").write_bytes(b"\x89PNG fake")

    facts = drain()
    assert len(facts) == 1
    assert facts[0].metadata["stream"] == "screenshots"
    assert not (shot / "shot-120000.png").exists()


def test_provenance_fallback_mtime(provider, make_session, drain):
    """Pre-C1 files (no download_captured event — e.g. the UOB PDF)
    drain with captured_at from the file and downloaded_by unknown."""
    sdir = make_session("uob")
    _stage(sdir, "old.pdf", PDF)
    old = time.time() - 86400
    os.utime(sdir / "downloads" / "old.pdf", (old, old))

    facts = drain()
    assert facts[0].metadata["captured_at"] is not None
    assert facts[0].metadata["url"] is None
    assert facts[0].metadata["downloaded_by"] == "unknown"


def test_unreadable_manifest_skipped(provider, sessions_root, drain):
    bad = sessions_root / "broken"
    bad.mkdir()
    (bad / "session.json").write_text("{not json")
    assert drain() == []


def test_empty_downloads_dir_pruned(provider, make_session, drain):
    """F2: zero local retention — the staged dir disappears with its file."""
    sdir = make_session("dbs")
    _stage(sdir, "stmt.pdf", PDF)
    drain()
    assert not (sdir / "downloads").exists()
