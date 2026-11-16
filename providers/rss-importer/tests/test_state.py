"""Feed-state store semantics — the provider's only true state."""

import json

from rss_importer.state import FeedStateStore, fill_defaults, new_feed_state


def test_roundtrip_and_defaults(tmp_path):
    path = tmp_path / "state.json"
    store = FeedStateStore(str(path))
    states = {"http://f.example/x": {"etag": 'W/"a"'}}  # partial entry
    store.save(states)
    loaded = store.load()
    # stored fields survive; new default fields are filled in (forward compat)
    assert loaded["http://f.example/x"]["etag"] == 'W/"a"'
    assert loaded["http://f.example/x"]["interval_s"] == 3600
    assert loaded["http://f.example/x"]["status"] == "active"
    assert loaded["http://f.example/x"]["full_fetch"] is False


def test_save_is_atomic_no_tmp_leftover(tmp_path):
    path = tmp_path / "state.json"
    FeedStateStore(str(path)).save({"u": {"etag": None}})
    assert path.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_corrupt_file_recovered(tmp_path):
    """A corrupt state file moves aside and the store starts fresh — losing
    the politeness cache is recoverable; wedging the provider is not."""
    path = tmp_path / "state.json"
    path.write_text("{not json")
    store = FeedStateStore(str(path))
    assert store.load() == {}
    assert not path.exists()  # moved to .corrupt
    assert path.with_suffix(".corrupt").exists()
    # and the store keeps working
    store.save({"u": new_feed_state()})
    assert store.load()["u"]["status"] == "active"


def test_missing_file_is_empty(tmp_path):
    assert FeedStateStore(str(tmp_path / "absent.json")).load() == {}


def test_non_object_state_file_rejected(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps(["a", "list"]))
    assert FeedStateStore(str(path)).load() == {}


def test_fill_defaults_preserves_unknown_fields():
    """Older/forward fields survive upgrades — the store never destroys
    what it doesn't know about."""
    merged = fill_defaults({"etag": "e", "some_future_field": 42})
    assert merged["etag"] == "e"
    assert merged["some_future_field"] == 42
    assert merged["consecutive_errors"] == 0
