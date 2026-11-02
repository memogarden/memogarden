"""Tests for the oldest-window search cache (hourly runs, 6h refresh)."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from system.soil import create_email_item

from conftest import simple_message
from gmail_importer import importer as importer_module
from gmail_importer.importer import GmailImporter
from gmail_importer.search_cache import SearchCache
from test_progressive import ScriptedGmail


@pytest.fixture
def cache_path(tmp_path):
    return tmp_path / "gmail-search-cache.json"


def make_run(soil, cache_path, monkeypatch):
    """Returns (run, calls) where run() executes one progressive pass and
    calls counts how many times the expensive search actually ran."""
    calls = {"search": 0}

    def fake_find(api_, target, verbose, page_budget):
        calls["search"] += 1
        return ScriptedGmail.oldest_window(target)

    def run():
        with soil:
            imp = GmailImporter(
                soil=soil,
                account_id="t@example.com",
                hacm_path="/tmp/x.enc",
                search_cache_path=str(cache_path),
            )
        monkeypatch.setattr(imp, "_build_api_client", lambda: ScriptedGmail())
        monkeypatch.setattr(importer_module, "find_oldest_message_ids", fake_find)
        return imp.import_next(limit=2, verbose=False)

    return run, calls


class TestSearchCache:
    def test_first_run_searches_and_writes_cache(self, importer, cache_path, monkeypatch):
        run, calls = make_run(importer.soil, cache_path, monkeypatch)
        run()
        assert calls["search"] == 1
        doc = json.loads(cache_path.read_text())
        assert doc["count"] == 6  # full oldest-first window stored

    def test_second_run_serves_from_cache(self, importer, cache_path, monkeypatch):
        run, calls = make_run(importer.soil, cache_path, monkeypatch)
        run()
        run()  # cursor=2: serves ids[2:4] from cache
        assert calls["search"] == 1  # no re-search within TTL
        # both runs fetched their own batches; a..d now ingested
        with importer.soil:
            total = len(importer.soil.list_items(_type="Email", limit=100))
        assert total == 4

    def test_stale_cache_triggers_refresh(self, importer, cache_path, monkeypatch):
        run, calls = make_run(importer.soil, cache_path, monkeypatch)
        run()
        doc = json.loads(cache_path.read_text())
        doc["cached_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=7)
        ).isoformat()
        cache_path.write_text(json.dumps(doc))
        run()
        assert calls["search"] == 2  # stale: full search reran

    def test_fresh_but_short_cache_triggers_refresh(self, importer, cache_path, monkeypatch):
        run, calls = make_run(importer.soil, cache_path, monkeypatch)
        run()
        run()
        assert calls["search"] == 1
        doc = json.loads(cache_path.read_text())
        assert len(doc["ids"]) == 6
        # cursor=4 now; cache (6 ids) covers 6 >= 6 -> still a hit for run 3
        run()
        assert calls["search"] == 1
        # shrink the cache below cursor+limit: refresh despite fresh age
        doc["ids"] = doc["ids"][:4]
        doc["count"] = 4
        cache_path.write_text(json.dumps(doc))
        run()
        assert calls["search"] == 2

    def test_corrupt_cache_treated_as_miss(self, importer, cache_path, monkeypatch):
        cache_path.write_text("{not json")
        run, calls = make_run(importer.soil, cache_path, monkeypatch)
        run()
        assert calls["search"] == 1  # ignored the corrupt file

    def test_exhaustion_read_from_cache_without_search(self, importer, cache_path, monkeypatch):
        run, calls = make_run(importer.soil, cache_path, monkeypatch)
        run()  # cursor=2, cache holds 6
        run()  # cursor=4
        run()  # cursor=6 >= 6: exhausted, read from cache
        assert calls["search"] == 1
        assert cache_path.exists()
