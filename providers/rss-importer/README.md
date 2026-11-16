# rss-importer

Ingests the operator's RSS/Atom feed subscriptions into MemoGarden as
**Entry** facts (schemas/entry.yaml), with the subscription list itself held
as operator-authored **Subscription** facts (schemas/subscription.yaml,
seeded via `memogarden import opml`). Raw feed responses and (opt-in)
full-fetched articles are archived in Soil as Attachment facts. Registered
provider name: `rss`.

Design of record: `plan/rss_provider_research.md` (decisions recorded
2026-11-16). Politeness follows its §5 policy — this provider is built to
pass a rachelbythebay feed-reader audit.

## Shape

- **Subscriptions in memogarden, not config**: the provider enumerates
  Subscription facts at sync start; `memogarden import opml --file feeds.opml`
  creates them idempotently (dedup on `xml_url`). Feed URLs are
  whitespace-stripped at import (the trailing-space 404 loop is a documented
  fetcher failure mode).
- **The feed is the artifact**: only feed URLs are fetched. Feed-provided
  content is stored verbatim in `Entry.data` (gmail-body precedent). Pages
  entries link to are never crawled; enclosures are pointers, never
  downloaded.
- **Archived artifacts**: every 200 response that delivered new entries is
  archived verbatim as an Attachment fact (`contained_by` the Subscription) —
  "as served by the publisher at time T". Opted-in feeds additionally fetch
  one page per NEW entry as article Attachment facts (`contained_by` the
  Entry): default OFF, new-entries-only, fetched-once.
- **Revisions**: a known `entry_id` re-served with a newer `updated` AND a
  different content hash yields a revision Note fact (`contained_by` the
  Entry). Timestamp-only touches churn nothing.
- **Identity** (per feed): guid → atom:id → rdf:about → normalized link →
  hash(title+published). Compared as opaque strings; never fetched/resolved.
- **Dedup**: done-set projection `(metadata.feed, data.entry_id)` rebuilt
  from Soil each sync — there is no cursor and no state file for correctness.
  The runtime's `since` is accepted and ignored (feed-keyed, not
  timestamp-keyed).

## State (deliberately minimal)

The provider's ONLY state is the politeness cache:
`~/.local/share/memogarden/rss-feed-state.json` — per-feed verbatim
validators (etag / last_modified), body hash, adaptive interval, next-due,
error/parse streaks, status (`active | gone | dead | paused`), and the
`full_fetch` opt-in. Atomic writes, saved incrementally per feed. Deleting
it is recoverable (worst case: one unconditional round of polls). Seen
entries and subscriptions live in memogarden itself.

## Politeness (research doc §5 — the load-bearing part)

- **Conditional GET always**: validators echoed VERBATIM (incl. `W/` and
  quotes), refreshed from every response — 304s included (RFC 9111 §4.3.4).
  No-validator feeds fall back to a body-hash skip. Validators are never
  invented and never derived from feed metadata.
- **Hourly base cadence** (the heartbeat executor is the floor), per-feed
  adaptive interval clamped [1 h, 24 h] — a monthly blog gets polled daily,
  not hourly. Feed hints honored as floors only: `<ttl>`,
  `Cache-Control: max-age`, RSS 1.0 syndication module; `<skipHours>` shifts
  a slot, capped at 25 h.
- **Errors**: 410 → gone (terminal); 404 streak (20 consecutive or 30 days)
  → tombstone with a weekly probe — host-level failures never count toward
  it; 429/503 honor Retry-After (60 min floor on 429); everything else
  full-jitter exponential backoff, capped at 7 days.
- **Never bursts**: serial sweeps with a floor between any two requests, a
  larger gap back to the same host, shuffled feed order, and a per-host
  circuit breaker (a dead site defers its other feeds without penalizing
  them).
- **Budget**: each sweep runs under a wall-clock budget (default 20 min);
  over-budget feeds are simply deferred — the runtime has no run timeout, so
  bounded sockets (15 s) + budget are mandatory (a hung sync would hold the
  single-flight lock until restart).
- **Identification**: `MemoGarden-RSS/0.1 (+https://memogarden.ngjunsiang.me/privacy/; 1 subscriber; feed-key: <8hex>)`
  — product/version, contact URL, honest subscriber count, per-feed key.
  No robots.txt consultation (feeds are explicitly published; 410 is the
  publisher's opt-out and is honored terminally).

## Provider registration

```bash
memogarden register provider \
  --name rss \
  --module rss_importer.provider \
  --class RssProvider \
  --interval 3600 \
  --description "RSS/Atom feed ingestion (polite conditional-GET poller)"
```

The module must be importable in the API server's venv — a one-line
`rss_importer.pth` in `memogarden-api/.venv/lib/python3.14/site-packages/`
pointing at this checkout (same mechanism as `tada_importer` /
`gmail_importer`), plus `feedparser` + `requests` in that venv. Then restart
`memogarden.service`. The origin pin (RFC-004 §12.2) already allowlists the
workspace `providers/` root.

Seed subscriptions first (the sweep is a clean no-op without any):

```bash
memogarden import opml --file ~/feeds.opml        # --dry-run to preview
```

## Config (registry `config` JSON; references only, no secrets)

| key | default | meaning |
|---|---|---|
| `state_path` | `~/.local/share/memogarden/rss-feed-state.json` | politeness-cache store |
| `artifacts_dir` | `~/.local/share/memogarden/attachments` | content-addressed archive (shared with gmail's store) |
| `request_timeout` | `15.0` | per-request socket timeout (s) |
| `sweep_budget_s` | `1200.0` | per-run wall-clock budget; over-budget feeds defer |
| `inter_request_sleep` | `1.0` | floor between any two requests (s) |
| `same_host_gap_s` | `3.0` | floor before returning to the same host (s) |
| `max_backoff_s` | `604800.0` | backoff cap (7 days) |
| `article_max_bytes` | `2000000` | full-fetch page size cap |
| `soil_path` | runtime default | explicit Soil path override |

Full-fetch is opted in per feed by setting `"full_fetch": true` on that
feed's entry in the state file (no CLI surface yet).

## Paused / dead feeds

3 consecutive parse errors pause a feed (Miniflux precedent) — resume by
setting its state back to `"active"` once the feed is fixed. 404-streak
tombstones probe weekly; 410 feeds are never contacted again. Data is never
deleted in any of these states.

## Tests

```bash
PYTHONPATH=providers/rss-importer memogarden-api/.venv/bin/python -m pytest providers/rss-importer/tests
```

62 tests: wire protocol against a real localhost HTTP server (validators,
304s, Retry-After, redirects, backoff table, tombstone heuristics), parsing
(identity chain, dates, content selection, bozo recovery), sweep behavior
(done-sets, revisions, archiving, breaker, budget, adaptive interval,
redirect adoption), and state-store semantics.
