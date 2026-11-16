"""RssProvider — the RSS/Atom sweep as a Provider Protocol implementation.

Design: plan/rss_provider_research.md (decisions recorded 2026-11-16).

- Subscriptions live in memogarden (Subscription facts, operator-seeded via
  OPML); the provider enumerates them at sync start. Seen entries live in
  memogarden (done-set projections). The provider's ONLY state is the
  politeness cache in its own feed-state store (state.FeedStateStore).
- The runtime's `since` timestamp is accepted and ignored (feed-keyed, not
  timestamp-keyed — same posture as tada/gmail).
- Each heartbeat run performs one serial sweep of due feeds: conditional GET,
  full-jitter backoff per the §5.3 table, per-host circuit breaker, per-run
  wall-clock budget (the runtime has no timeout — a hung sync would hold the
  single-flight lock until restart, so bounded sockets + budget are
  mandatory), 410 → gone, 404 streak → tombstone, Retry-After honored.
- New entries yield as Entry facts; a raw-response Attachment fact lands with
  them (provenance, decision §8.2); publisher edits yield revision Note
  facts (§8.3); opted-in feeds additionally fetch one page per new entry as
  article Attachment facts (§4.5).
- Per-feed failures are swallowed (gmail _fetch_batch pattern): there is no
  partial-success run status, so the state store records per-feed health and
  only a wholesale failure raises.
"""

from __future__ import annotations

import hashlib
import random
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from rss_importer import fetcher, store, translate
from rss_importer.state import DEFAULT_STATE_PATH, FeedStateStore, new_feed_state

# Relation created_at convention shared with the other providers:
# day-granularity ints from this epoch (RFC-002 "days since epoch").
_RELATION_EPOCH = datetime(2020, 1, 1, tzinfo=UTC)

_DEFAULTS = {
    "state_path": DEFAULT_STATE_PATH,
    "artifacts_dir": store.DEFAULT_ARTIFACTS_DIR,
    "request_timeout": 15.0,
    # per-run wall-clock budget — over-budget feeds are deferred, not failed
    "sweep_budget_s": 1200.0,
    # minimum gap between ANY two requests (never burst at startup)
    "inter_request_sleep": 1.0,
    # minimum gap before returning to the same host (per-host pacing)
    "same_host_gap_s": 3.0,
    # backoff cap
    "max_backoff_s": 604800.0,
    # full-fetch article cap (bytes; verbatim page archive)
    "article_max_bytes": 2_000_000,
    "soil_path": None,
}

# interval clamps for the adaptive schedule (research doc §5.1)
_MIN_INTERVAL_S = 3600
_MAX_INTERVAL_S = 86400
# consecutive parse errors before pausing auto-polls (Miniflux precedent)
_PARSE_ERROR_LIMIT = 3
# 404-streak tombstone thresholds (research doc §5.3)
_DEAD_STREAK = 20
_DEAD_DAYS = 30
# dead feeds get one probe a week, never deletion
_DEAD_PROBE_S = 7 * 86400


class RssProvider:
    """Provider Protocol implementation over HTTP feed polling."""

    def __init__(self, config: dict):
        merged = {**_DEFAULTS, **(config or {})}
        expand = lambda p: str(Path(p).expanduser())  # noqa: E731
        self._state_path = expand(merged["state_path"])
        self._artifacts_dir = expand(merged["artifacts_dir"])
        self._timeout = float(merged["request_timeout"])
        self._budget_s = float(merged["sweep_budget_s"])
        self._sleep_s = float(merged["inter_request_sleep"])
        self._host_gap_s = float(merged["same_host_gap_s"])
        self._max_backoff_s = float(merged["max_backoff_s"])
        self._article_max_bytes = int(merged["article_max_bytes"])
        self._states = FeedStateStore(self._state_path)

        if merged.get("soil_path"):
            from system.soil import Soil

            soil = Soil(expand(merged["soil_path"]))
        else:
            from system.soil import get_soil

            soil = get_soil()
        self._soil = soil
        with soil:
            # Construction AND schema init (idempotent), like the other providers.
            soil.init_schema()

    def _log(self, msg: str) -> None:
        print(f"[rss provider] {msg}")

    # ------------------------------------------------------------------
    # Provider Protocol
    # ------------------------------------------------------------------

    def sync(self, since: str | None = None) -> Iterator[dict]:
        """Yield facts for every due feed's new entries / revisions /
        archived responses. Args:
            since: Ignored — the feed body is the delta each poll.

        One exception per feed never fails the run (it lands in that feed's
        state); only a wholesale breakdown (subscriptions unreadable etc.)
        propagates, mirroring the per-item-error house pattern.
        """
        started = time.monotonic()
        subscriptions = self._subscriptions()
        if not subscriptions:
            self._log("no Subscription facts — seed with `memogarden import opml`")
            return

        states = self._states.load()
        done_entries = self._ingested_entry_keys()
        done_revisions = self._ingested_revision_keys()
        stored_entries = self._stored_entry_index()

        now_iso = datetime.now(UTC).isoformat()
        due = self._due_feeds(subscriptions, states, now_iso)
        # Persistent offsets come from next_due_at; the sweep order itself is
        # shuffled so no feed is structurally always-first (no thundering herd).
        random.shuffle(due)
        self._log(
            f"sweep: {len(due)}/{len(subscriptions)} feed(s) due "
            f"(budget {self._budget_s:.0f}s)"
        )

        failed_hosts: set[str] = set()
        last_host: str | None = None
        deferred = 0

        for feed_url in due:
            if time.monotonic() - started >= self._budget_s:
                deferred += 1
                continue
            state = states[feed_url]
            if state.get("status") == "gone":
                continue
            host = fetcher.host_of(feed_url)
            if host and host in failed_hosts:
                # circuit breaker: the site is failing; don't hit its other
                # feeds this sweep, and don't count it against them
                deferred += 1
                continue

            self._politeness_pause(host, last_host)
            try:
                yield from self._sync_feed(
                    feed_url, state, done_entries, done_revisions, stored_entries,
                    failed_hosts,
                )
                if state.get("last_status") is None and state.get("last_error"):
                    # transport failure — trip the host breaker for the rest
                    # of this sweep
                    if host:
                        failed_hosts.add(host)
                else:
                    failed_hosts.discard(host)
            except Exception as e:  # noqa: BLE001 — one bad feed ≠ a failed run
                self._log(f"{feed_url}: unexpected error ({type(e).__name__}: {e})")
                state["consecutive_errors"] = int(state.get("consecutive_errors") or 0) + 1
                state["last_error"] = f"{type(e).__name__}: {e}"
                self._backoff(feed_url, state, None, None)
                if host:
                    failed_hosts.add(host)

            self._states.save(states)
            last_host = host

        if deferred:
            self._log(
                f"deferred {deferred} feed(s) — budget/breaker; they stay due"
            )

    def create_relations(self, fact) -> list:
        """Link yielded facts to their parents (contained_by), mirroring the
        Attachment→Email and Note→Task precedents:
            Entry      -> Subscription (by metadata.feed = data.xml_url)
            Note       -> Entry         (revision notes, by feed + entry_id)
            Attachment -> Subscription (feed-response archives, by feed)
            Attachment -> Entry         (article archives, by feed + entry_id)
        Resolutions are projected json_extract lookups against the runtime's
        already-committed rows (the per-fact commit contract)."""
        kind = getattr(fact, "_type", None)
        metadata = getattr(fact, "metadata", None) or {}
        if metadata.get("source") != "rss":
            return []
        feed_url = metadata.get("feed")

        target_query = None  # (sql, params, description)
        if kind == "Entry" and feed_url:
            target_query = (
                "SELECT uuid FROM item WHERE _type = 'Subscription'"
                " AND json_extract(data, '$.xml_url') = ?"
                " ORDER BY canonical_at ASC LIMIT 1",
                (feed_url,),
            )
        elif kind == "Note" and feed_url and metadata.get("entry_id"):
            target_query = (
                "SELECT uuid FROM item WHERE _type = 'Entry'"
                " AND json_extract(metadata, '$.source') = 'rss'"
                " AND json_extract(metadata, '$.feed') = ?"
                " AND json_extract(data, '$.entry_id') = ?"
                " ORDER BY canonical_at ASC LIMIT 1",
                (feed_url, metadata["entry_id"]),
            )
        elif kind == "Attachment" and feed_url and metadata.get("artifact"):
            if metadata["artifact"] == "feed-response":
                target_query = (
                    "SELECT uuid FROM item WHERE _type = 'Subscription'"
                    " AND json_extract(data, '$.xml_url') = ?"
                    " ORDER BY canonical_at ASC LIMIT 1",
                    (feed_url,),
                )
            elif metadata["artifact"] == "article" and metadata.get("entry_id"):
                target_query = (
                    "SELECT uuid FROM item WHERE _type = 'Entry'"
                    " AND json_extract(metadata, '$.source') = 'rss'"
                    " AND json_extract(metadata, '$.feed') = ?"
                    " AND json_extract(data, '$.entry_id') = ?"
                    " ORDER BY canonical_at ASC LIMIT 1",
                    (feed_url, metadata["entry_id"]),
                )
        if target_query is None:
            return []

        sql, params = target_query
        with self._soil as soil:
            row = soil._get_connection().execute(sql, params).fetchone()
        if row is None:
            return []

        from system.soil import generate_soil_uuid
        from system.soil.relation import SystemRelation

        days = (datetime.now(UTC) - _RELATION_EPOCH).days
        return [
            SystemRelation(
                uuid=generate_soil_uuid(),
                kind="contained_by",
                source=fact.uuid,
                source_type="item",
                target=row["uuid"],
                target_type="item",
                created_at=days,
                evidence=None,
                metadata=None,
            )
        ]

    # ------------------------------------------------------------------
    # Per-feed sweep
    # ------------------------------------------------------------------

    def _sync_feed(
        self,
        feed_url: str,
        state: dict,
        done_entries: set[tuple[str, str]],
        done_revisions: set[tuple[str, str, str]],
        stored_entries: dict[tuple[str, str], tuple[str | None, str | None]],
        failed_hosts: set[str] | None = None,
    ) -> Iterator[dict]:
        """Fetch one feed, update its state, yield its facts. All error
        classes land in `state` — nothing raises. Transport failures also
        register the host in `failed_hosts` (the sweep-level circuit
        breaker skips the host's remaining feeds this sweep)."""
        host = fetcher.host_of(feed_url)
        fetch_url = state.get("fetch_url") or feed_url
        key = fetcher.feed_key_for(feed_url)
        result = fetcher.fetch(fetch_url, state, key, timeout=self._timeout)
        now_iso = datetime.now(UTC).isoformat()
        now = datetime.now(UTC)

        # Transport failure — never counts toward feed-death streaks
        # ("site down" is not "feed gone") — but trips the host breaker.
        if result.error is not None:
            state["consecutive_errors"] = int(state.get("consecutive_errors") or 0) + 1
            state["last_status"] = None
            state["last_error"] = result.error
            self._log(f"{feed_url}: transport failure ({result.error})")
            self._backoff(feed_url, state, None, None)
            if failed_hosts is not None and host:
                failed_hosts.add(host)
            return

        state["last_status"] = result.status_code
        state["last_error"] = None

        # Terminal: 410 Gone — the publisher's documented opt-out.
        if result.status_code == 410:
            state["status"] = "gone"
            state["next_due_at"] = None
            self._log(f"{feed_url}: 410 Gone — stopped polling (data retained)")
            return

        # 304 Not Modified: refresh validators from the response (RFC 9111
        # §4.3.4), reset streaks, advance the schedule.
        if result.status_code == 304:
            self._adopt_validators(state, result, feed_url)
            state["consecutive_errors"] = 0
            state["parse_errors"] = 0
            state["first_error_at"] = None
            self._schedule(feed_url, state, now)
            self._log(f"{feed_url}: 304 not modified")
            return

        # 200: update validators BEFORE any early-return (the NetNewsWire
        # bug: skipping parse must not skip validator refresh).
        if result.status_code == 200:
            self._adopt_validators(state, result, feed_url)
            body_hash = hashlib.sha256(result.body).hexdigest()

            redirect_note = ""
            if result.redirect_hops and result.final_url and state.get("fetch_url") != result.final_url:
                # adopt stable redirect targets into fetch_url after the
                # first sighting; identity (xml_url) never changes (§7.4)
                state["fetch_url"] = result.final_url
                redirect_note = f" (redirect → {result.final_url})"

            if body_hash == state.get("body_hash"):
                # repeat 200 with identical bytes — nothing to learn
                state["consecutive_errors"] = 0
                state["parse_errors"] = 0
                state["first_error_at"] = None
                self._schedule(feed_url, state, now)
                self._log(f"{feed_url}: 200 unchanged{redirect_note}")
                return

            parsed = translate.parse_feed(result.body, fetch_url)
            bozo = bool(parsed.get("bozo"))
            entries = parsed.get("entries") or []
            if bozo and not entries:
                state["parse_errors"] = int(state.get("parse_errors") or 0) + 1
                # NOTE: body_hash is deliberately NOT stored here — an
                # identical unparseable body must re-parse each sweep so the
                # streak can accumulate (a stored hash would route every
                # later sweep into the unchanged-200 skip, which also resets
                # this streak).
                detail = str(parsed.get("bozo_exception") or "unparseable")
                if state["parse_errors"] >= _PARSE_ERROR_LIMIT:
                    state["status"] = "paused"
                    self._log(
                        f"{feed_url}: parse error streak {state['parse_errors']} "
                        f"— paused auto-polling ({detail})"
                    )
                    self._backoff(feed_url, state, result.status_code, None)
                else:
                    self._log(
                        f"{feed_url}: parse error {state['parse_errors']}/"
                        f"{_PARSE_ERROR_LIMIT} ({detail})"
                    )
                    self._backoff(feed_url, state, result.status_code, None)
                return

            feed_title = translate.feed_title_of(parsed)
            # Persist feed-declared hints with the state: a 304 sweep has no
            # body to re-parse, so ttl/skipHours floors must survive from the
            # last 200 (research doc §5.1 — floors, never mandates).
            state["hints"] = {
                **fetcher.parse_hints(result.body),
                "max_age_s": fetcher.parse_max_age(result.headers),
            }
            yield from self._ingest_entries(
                feed_url,
                feed_title,
                entries,
                bozo,
                result,
                body_hash,
                done_entries,
                done_revisions,
                stored_entries,
                state,
                now,
            )
            return

        # Every remaining status is an error class (404 / 429 / 503 / others).
        state["consecutive_errors"] = int(state.get("consecutive_errors") or 0) + 1
        if state["consecutive_errors"] == 1:
            state["first_error_at"] = now_iso
        retry_after = result.retry_after_s
        state["last_error"] = f"HTTP {result.status_code}"
        self._log(
            f"{feed_url}: HTTP {result.status_code}"
            + (f" retry-after {retry_after:.0f}s" if retry_after else "")
        )

        if fetcher.tombstone_dead(
            result.status_code,
            state["consecutive_errors"],
            state.get("first_error_at"),
            now_iso,
        ):
            state["status"] = "dead"
            state["next_due_at"] = (now + timedelta(seconds=_DEAD_PROBE_S)).isoformat()
            self._log(
                f"{feed_url}: 404 streak {state['consecutive_errors']} — "
                f"tombstoned, weekly probe"
            )
            return

        self._backoff(feed_url, state, result.status_code, retry_after)

    def _ingest_entries(
        self,
        feed_url: str,
        feed_title: str | None,
        entries: list,
        bozo: bool,
        result,
        body_hash: str,
        done_entries: set[tuple[str, str]],
        done_revisions: set[tuple[str, str, str]],
        stored_entries: dict[tuple[str, str], tuple[str | None, str | None]],
        state: dict,
        now: datetime,
    ) -> Iterator[dict]:
        """Yield new Entry facts, revision Notes, the archived-response
        Attachment, and (for opted-in feeds) article Attachments; then reset
        the feed's health and advance its schedule."""
        realized_at = now.isoformat()
        new_entries = []
        revisions = []
        for entry in entries:
            entry_id = translate.entry_identity(entry)
            key = (feed_url, entry_id)
            if key not in done_entries:
                new_entries.append(entry)
                continue
            # known entry — revision detection (§4.2 decision): newer
            # `updated` AND different content hash; timestamp-only
            # touches churn nothing.
            stored_updated, stored_hash = stored_entries.get(key, (None, None))
            _, updated = translate.entry_dates(entry)
            description, _ = translate.entry_content(entry)
            new_hash = translate.content_hash(description) if description else None
            if (
                stored_updated
                and updated
                and updated > stored_updated
                and stored_hash
                and new_hash
                and new_hash != stored_hash
                and (feed_url, entry_id, updated) not in done_revisions
            ):
                revisions.append(entry)

        if not new_entries and not revisions:
            state["consecutive_errors"] = 0
            state["parse_errors"] = 0
            state["first_error_at"] = None
            state["body_hash"] = body_hash
            self._schedule(feed_url, state, now)
            self._log(f"{feed_url}: 200, {len(entries)} entr(ies), nothing new")
            return

        # Archive the raw response verbatim — only when it delivered
        # something (decision §8.2: provenance where provenance is due).
        stored = store.store_bytes(self._artifacts_dir, result.body, ".xml")
        yield translate.feed_response_attachment_fact(
            feed_url, feed_title, realized_at, stored
        )

        for entry in new_entries:
            fact = translate.entry_fact(entry, feed_url, feed_title, realized_at, bozo)
            done_entries.add((feed_url, fact["data"]["entry_id"]))
            stored_entries[(feed_url, fact["data"]["entry_id"])] = (
                fact["data"].get("updated"),
                fact["data"].get("content_hash"),
            )
            yield fact

        for entry in revisions:
            note = translate.revision_note_fact(entry, feed_url, realized_at)
            done_revisions.add(
                (feed_url, note["metadata"]["entry_id"], note["data"]["revised_at"])
            )
            yield note

        # Full-article fetches: opt-in per feed, one page per NEW entry,
        # fetched once, budget-capped (research doc §4.5). Default OFF.
        if state.get("full_fetch") and new_entries:
            yield from self._fetch_articles(feed_url, new_entries, realized_at)

        state["body_hash"] = body_hash
        state["consecutive_errors"] = 0
        state["parse_errors"] = 0
        state["first_error_at"] = None
        self._adapt_interval(state, now)
        self._schedule(feed_url, state, now)
        self._log(
            f"{feed_url}: +{len(new_entries)} new, +{len(revisions)} revision(s)"
            + (f", archived {stored['size']}B" if stored else "")
        )

    def _fetch_articles(
        self, feed_url: str, new_entries: list, realized_at: str
    ) -> Iterator[dict]:
        """One verbatim page archive per new entry for opted-in feeds."""
        for entry in new_entries:
            link = translate.normalize_link(entry.get("link")) or entry.get("link")
            if not link:
                continue
            time.sleep(self._sleep_s)
            result = fetcher.fetch(
                link, {}, fetcher.feed_key_for(feed_url), timeout=self._timeout
            )
            if result.error or result.status_code != 200 or not result.body:
                self._log(f"{feed_url}: article fetch failed ({link})")
                continue
            if len(result.body) > self._article_max_bytes:
                self._log(f"{feed_url}: article too large, skipped ({link})")
                continue
            entry_id = translate.entry_identity(entry)
            stored = store.store_bytes(self._artifacts_dir, result.body, ".html")
            yield translate.article_attachment_fact(
                feed_url, entry_id, link, realized_at, stored
            )

    # ------------------------------------------------------------------
    # Schedule, backoff, hints
    # ------------------------------------------------------------------

    def _adopt_validators(self, state: dict, result, feed_url: str) -> None:
        """Store the response's validators verbatim — including from a 304
        (RFC 9111 §4.3.4). Never parse-and-requote (RFC 9110 §8.8.3)."""
        state["etag"] = result.etag
        state["last_modified"] = result.last_modified

    def _backoff(
        self, feed_url: str, state: dict, status_code: int | None, retry_after: float | None
    ) -> None:
        """Error path: compute the §5.3 delay, apply full jitter and the cap,
        advance next_due."""
        now = datetime.now(UTC)
        base = max(float(state.get("interval_s") or _MIN_INTERVAL_S), _MIN_INTERVAL_S)
        delay = fetcher.error_backoff_s(
            status_code,
            int(state.get("consecutive_errors") or 1),
            retry_after,
            base,
            self._max_backoff_s,
        )
        delay = min(max(fetcher.apply_jitter(delay), 60.0), self._max_backoff_s)
        state["next_due_at"] = (now + timedelta(seconds=delay)).isoformat()

    def _schedule(self, feed_url: str, state: dict, now: datetime) -> None:
        """Happy path: effective interval from the adaptive schedule, raised
        by feed-declared hints (floors only), jittered, clamped to [1h, 24h].
        skipHours shifts the poll to the next sweep rather than pushing past
        a 25h staleness bound."""
        interval = float(state.get("interval_s") or _MIN_INTERVAL_S)
        interval = min(max(interval, _MIN_INTERVAL_S), _MAX_INTERVAL_S)

        # Feed hints as floors — from the last 200 response (persisted in
        # state, since a 304 sweep has no body to re-parse).
        hints = state.get("hints") or {}
        if hints.get("ttl_minutes"):
            interval = max(interval, hints["ttl_minutes"] * 60)
        if hints.get("max_age_s"):
            interval = max(interval, float(hints["max_age_s"]))
        if hints.get("sy_period_s"):
            interval = max(interval, float(hints["sy_period_s"]))
        interval = min(interval, _MAX_INTERVAL_S)

        skip = hints.get("skip_hours")
        due = now + timedelta(seconds=fetcher.apply_jitter(interval))
        if skip and fetcher.skip_hours_hit(skip, due):
            # defer to the first minute after the skipped hour; never let a
            # skip delay a feed more than ~25h (a skipped hour must not
            # become a missed day)
            due = min(
                due + timedelta(hours=1),
                now + timedelta(hours=25),
            )
        state["next_due_at"] = due.isoformat()

    def _adapt_interval(self, state: dict, now: datetime) -> None:
        """Adaptive scheduling (§5.1): blend the observed inter-update interval
        into the schedule, updated only on sweeps that delivered entries,
        clamped [1h, 24h], changed only after ≥3 observations. Blending (not
        overwriting) lets slow feeds' intervals GROW toward their observed
        cadence — a monthly blog gets polled daily, not hourly."""
        last_change = state.get("last_change_at")
        if last_change:
            try:
                observed = (now - datetime.fromisoformat(last_change)).total_seconds()
            except ValueError:
                observed = None
            if observed and observed > 0:
                count = int(state.get("changes_count") or 0)
                current = float(state.get("interval_s") or _MIN_INTERVAL_S)
                if count >= 3:
                    blended = 0.75 * current + 0.25 * observed
                    state["interval_s"] = int(
                        min(max(blended, _MIN_INTERVAL_S), _MAX_INTERVAL_S)
                    )
                else:
                    state["changes_count"] = count + 1
        state["last_change_at"] = now.isoformat()

    def _politeness_pause(self, host: str | None, last_host: str | None) -> None:
        """Serial sweeps pace themselves: a floor between ANY two requests,
        a larger one when returning to the same host."""
        gap = self._sleep_s
        if host and last_host and host == last_host:
            gap = max(gap, self._host_gap_s)
        if gap > 0:
            time.sleep(gap)

    # ------------------------------------------------------------------
    # Soil projections (the done-sets — correctness lives in memogarden)
    # ------------------------------------------------------------------

    def _subscriptions(self) -> list[str]:
        with self._soil as soil:
            rows = soil._get_connection().execute(
                """SELECT json_extract(data, '$.xml_url') AS url FROM item
                   WHERE _type = 'Subscription' AND superseded_by IS NULL"""
            ).fetchall()
        urls = []
        for row in rows:
            url = (row["url"] or "").strip()
            if url.startswith(("http://", "https://")):
                urls.append(url)
        return list(dict.fromkeys(urls))  # dedupe, keep order

    def _ingested_entry_keys(self) -> set[tuple[str, str]]:
        with self._soil as soil:
            rows = soil._get_connection().execute(
                """SELECT json_extract(metadata, '$.feed') AS feed,
                          json_extract(data, '$.entry_id') AS eid
                   FROM item
                   WHERE _type = 'Entry'
                     AND json_extract(metadata, '$.source') = 'rss'"""
            ).fetchall()
        return {(r["feed"], r["eid"]) for r in rows if r["feed"] and r["eid"]}

    def _ingested_revision_keys(self) -> set[tuple[str, str, str]]:
        with self._soil as soil:
            rows = soil._get_connection().execute(
                """SELECT json_extract(metadata, '$.feed') AS feed,
                          json_extract(metadata, '$.entry_id') AS eid,
                          json_extract(data, '$.revised_at') AS rat
                   FROM item
                   WHERE _type = 'Note'
                     AND json_extract(metadata, '$.source') = 'rss'
                     AND json_extract(metadata, '$.revision') = 1"""
            ).fetchall()
        return {(r["feed"], r["eid"], r["rat"]) for r in rows if r["feed"] and r["eid"] and r["rat"]}

    def _stored_entry_index(
        self,
    ) -> dict[tuple[str, str], tuple[str | None, str | None]]:
        """(feed, entry_id) → (updated, content_hash) for revision detection —
        projected so descriptions never load (the 700 MB lesson)."""
        with self._soil as soil:
            rows = soil._get_connection().execute(
                """SELECT json_extract(metadata, '$.feed') AS feed,
                          json_extract(data, '$.entry_id') AS eid,
                          json_extract(data, '$.updated') AS upd,
                          json_extract(data, '$.content_hash') AS chash
                   FROM item
                   WHERE _type = 'Entry'
                     AND json_extract(metadata, '$.source') = 'rss'"""
            ).fetchall()
        return {
            (r["feed"], r["eid"]): (r["upd"], r["chash"])
            for r in rows
            if r["feed"] and r["eid"]
        }

    def _due_feeds(
        self, subscriptions: list[str], states: dict[str, dict], now_iso: str
    ) -> list[str]:
        now = datetime.fromisoformat(now_iso)
        due = []
        for url in subscriptions:
            state = states.get(url)
            if state is None:
                state = new_feed_state()
                states[url] = state
            if state.get("status") == "gone":
                continue
            if state.get("status") == "paused":
                continue  # resumed manually
            next_due = state.get("next_due_at")
            if not next_due:
                due.append(url)
                continue
            try:
                if datetime.fromisoformat(next_due) <= now:
                    due.append(url)
            except ValueError:
                due.append(url)
        return due
