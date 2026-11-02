"""Local cache for Gmail oldest-window search results.

The hourly ingestion job runs against cached search results; the full
search (the expensive part — a couple of hundred list calls) only reruns
when the cache is older than the refresh interval, or when the run needs
ids beyond what the cache covers (e.g. the cursor reached the cached
window's end). Cache misses are self-healing: a corrupt or unreadable
cache file is ignored and treated as a miss.

Cache files record the oldest-first id window. The underlying Gmail ids
themselves are stable for search purposes (attachment ids are not — see
attachments.py); a user deleting/trashing an old mail makes its cached id
unfetchable, which the importer counts as a skipped/error item rather
than failing the run.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_TTL_HOURS = 6.0


class SearchCache:
    """File-backed cache of the enumerated oldest-first message-id window."""

    def __init__(self, path: Path, ttl_hours: float = DEFAULT_TTL_HOURS):
        self.path = Path(path)
        self.ttl = timedelta(hours=ttl_hours)

    def load_valid(self, min_length: int) -> Optional[list[str]]:
        """Cached oldest-first ids, if fresh AND long enough for the request.

        min_length = cursor + limit: a cache shorter than what the batch
        needs triggers a refresh even when fresh, otherwise the run would
        falsely report the mailbox exhausted.
        """
        try:
            doc = json.loads(self.path.read_text())
            ids = doc["ids"]
            cached_at = datetime.fromisoformat(doc["cached_at"])
        except (OSError, ValueError, KeyError, TypeError) as e:
            logger.info("Search cache unusable (%s); performing full search", e)
            return None
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            return None
        age = datetime.now(timezone.utc) - cached_at
        if age >= self.ttl:
            logger.info("Search cache stale (age %.1fh >= %.0fh); refreshing", age.total_seconds() / 3600, self.ttl.total_seconds() / 3600)
            return None
        if len(ids) < min_length:
            logger.info(
                "Search cache covers %d ids, need %d; refreshing", len(ids), min_length
            )
            return None
        return ids

    def store(self, oldest_first_ids: list[str]) -> None:
        """Persist the window atomically (write temp, rename)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        doc = {
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "count": len(oldest_first_ids),
            "ids": oldest_first_ids,
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(doc))
        tmp.rename(self.path)
