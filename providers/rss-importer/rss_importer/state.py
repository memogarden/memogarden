"""Feed-state store — the provider's only true state (decision: app-internal).

One entry per subscribed feed in a single JSON file
(`~/.local/share/memogarden/rss-feed-state.json`), holding the politeness
cache: verbatim HTTP validators, body hash, per-feed schedule and error
streaks. This is cache, not memory — seen entries are remembered by memogarden
itself (the done-set), subscriptions are Subscription facts. Written atomically
(.tmp + rename, the gmail historyId convention) and saved incrementally so a
crash keeps per-feed progress.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_STATE_PATH = "~/.local/share/memogarden/rss-feed-state.json"

# Fields every feed-state entry carries. Everything else in the stored dicts
# is preserved as-is (forward compatibility).
_DEFAULT_FEED_STATE = {
    # adopted stable-301 target (identity stays the original xml_url)
    "fetch_url": None,
    # verbatim HTTP validators — echoed byte-for-byte (RFC 9110 §8.8.3)
    "etag": None,
    "last_modified": None,
    # SHA-256 of the decompressed 200 body — the no-validator fallback and
    # the repeat-200 skip
    "body_hash": None,
    # adaptive interval, clamped [3600, 86400] by the provider
    "interval_s": 3600,
    # per-feed politeness scheduler; None = due now
    "next_due_at": None,
    # error bookkeeping (transport/HTTP streak vs parse streak, counted
    # separately: "feed broken" vs "feed unparseable")
    "consecutive_errors": 0,
    "parse_errors": 0,
    "first_error_at": None,
    "last_status": None,
    "last_error": None,
    # active | gone (410, terminal) | dead (tombstone, weekly probe) | paused
    # (3+ consecutive parse errors; resumed manually)
    "status": "active",
    # adaptive-scheduling memory
    "last_change_at": None,
    "changes_count": 0,
    # operator opt-in for full-article fetches (dormant by default)
    "full_fetch": False,
}


def new_feed_state() -> dict:
    """A fresh per-feed state dict (caller may override fields)."""
    return dict(_DEFAULT_FEED_STATE)


def fill_defaults(state: dict | None) -> dict:
    """Return `state` with every default field present (stored dicts from
    older provider versions are upgraded in place on load)."""
    merged = new_feed_state()
    if state:
        merged.update(state)
    return merged


class FeedStateStore:
    """Load/save the feed-state JSON file."""

    def __init__(self, path: str | os.PathLike = DEFAULT_STATE_PATH):
        self.path = Path(path).expanduser()

    def load(self) -> dict[str, dict]:
        """Read all feed states, upgrading stored entries with any new
        default fields. A corrupt file is renamed aside and replaced with a
        fresh store — losing the politeness cache is recoverable (worst case:
        one unconditional round of polls); wedging the provider is not."""
        path = self.path
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text())
            if not isinstance(raw, dict):
                raise ValueError("state file must be a JSON object")
        except (json.JSONDecodeError, ValueError, OSError) as e:
            backup = path.with_suffix(".corrupt")
            try:
                path.replace(backup)
            except OSError:
                pass
            print(f"[rss provider] state file unreadable ({e}); moved to {backup}")
            return {}
        return {url: fill_defaults(state) for url, state in raw.items()}

    def save(self, states: dict[str, dict]) -> None:
        """Atomically persist all feed states."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(states, indent=2, sort_keys=True))
        tmp.replace(self.path)
