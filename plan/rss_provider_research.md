# RSS Provider — Research & Proposed Design

**Status**: implemented + live — `providers/rss-importer` shipped
2026-11-16/18 (decisions §8 and sequence §9 executed; `memogarden-readly`
is the first consumer app, live at readly.ngjunsiang.me). This document
remains the design of record for the provider.
**Date**: 2026-11-16 (implemented 2026-11-16/18; status line corrected 2026-11-21).
**Companion sources**: rachelbythebay feed-reader behavior posts (site offline; all
links are verified Wayback captures), RFC 9110/9111/6585, RSS Advisory Board
specification + Best Practices Profile, feedparser docs, Miniflux/NewsBlur/
FreshRSS/Feedbin fetcher engineering, MemoGarden provider survey (this workspace).

---

## 1. Executive summary — the three questions

**Q1. What does the provider provide — URL, or full page?**
The **feed is the artifact**. The provider fetches only feed URLs, never the
pages feeds link to. Each feed entry becomes an immutable `Entry` fact carrying
its identity (`guid`/`atom:id`), title, link URL, dates, author, and the
feed-provided content (description / `content:encoded` / Atom content) stored
as-is — the same shape gmail uses for bodies (`data.description` +
`content_type`, HTML untruncated in data). Enclosures are stored as
url/type/length metadata, never downloaded. Full-page fetching is a *different
politeness class* — **amended by decision 2026-11-16**: the provider may
full-fetch articles for operator-opted-in feeds (one fetch per new entry,
fetched-once), stored as separate documents (§4.5); the default remains
feed-only, and read-time user-initiated fetches are always allowed.

**Q2. How can the provider keep internal state minimal?** — **DECIDED
2026-11-16**: memogarden receives only *processed information* (entries,
subscription facts); the raw-feed machinery is the app's own internal state
and never touches the fact store. Three tiers:

| Tier | Content | Lives in |
|---|---|---|
| Correctness (seen entries) | done-set of entry ids | **derived from memogarden facts** each sync — SQL `json_extract` projection over `Entry` facts (exact tada/gmail pattern). RSS has no incremental API (no gmail `historyId` analogue), so there is no cursor to store: the feed body *is* the delta, the done-set *is* the memory. |
| Real-world info (what to poll) | subscription list | **`Subscription` facts in memogarden**, seeded from the user's OPML. The provider enumerates Subscription facts at sync start; no config file. |
| Protocol cache (politeness) | per-feed ETag / Last-Modified / body-hash / next-due / error streaks | **provider-managed internal state** (§3.3) — DECIDED: "raw feed data isn't information for memogarden (that's only after processing)"; the app tracks each feed and passes new URLs + metadata to memogarden as they arrive. |

The correctness tier must live in memogarden regardless (dedup is
application-level lookup-then-insert; there is no DB hash-unique index). The
protocol-cache tier deliberately does NOT: it is cache, not memory, so it
follows the gmail `historyId` precedent — provider-managed, invisible to the
fact store. Promotion to a queryable core.db registry table remains available
later if per-feed health reporting is ever wanted.

**Q3. Politeness** — see §5 for the full policy. Headlines: conditional GET on
every request with verbatim validator echo; hourly default interval (the
heartbeat executor *is* the floor); per-feed adaptive interval clamped
[1 h, 24 h]; full-jitter exponential backoff per error class; honor
Retry-After; 410 → stop forever, 404 → streak to tombstone; serial sweep with
a per-host circuit breaker and a per-run wall-clock budget; honest User-Agent
with contact URL and subscriber count 1; gzip; no robots.txt consultation
(Google Feedfetcher precedent); WebSub rejected for this scale.

---

## 2. "URL or full page?" — the etiquette answer

The feed is an explicitly published machine-consumption artifact; the pages it
links to are not, for a poller's purposes. Two case studies bracket the rule:

- **Tiny Tiny RSS, 2018-04-12** (rachelbythebay): after fetching a 443 KB
  full-content feed, it crawled *every post page the feed links to* — 101
  requests in 17 seconds. Her verdict: the whole point of a full-content feed
  is that you don't fetch the linked posts.
  https://web.archive.org/web/20180413073124/https://rachelbythebay.com/w/2018/04/12/badfeed/
- **NewsBlur** gates original-page fetching behind a paid scraping proxy with
  its own error quotas (`SCRAPINGBEE_SKIP_AFTER_ERRORS = 3`) — i.e. the
  largest independent reader treats page-fetching as an exceptional,
  quarantined path, never part of the poll loop.
  https://github.com/samuelclay/newsblur/blob/main/utils/feed_fetcher.py

So for MemoGarden: the provider's footprint on a publisher is **exactly one
request per poll** (conditional GET → ideally one 304). Feed-provided content
is stored verbatim in `Entry.data`; images inside content are not fetched
server-side (they render/lazy-load client-side when the entry is read);
enclosure URLs (podcasts) are recorded, never auto-downloaded. Full-page
extraction is **decision-amended (§4.5)**: operator-opted-in feeds can be
fetched one page per new entry (fetched-once) with the article stored as a
separate document; the default footprint stays feed-only, and read-time
user-initiated fetches remain the browser-sessions stack.

---

## 3. State model in detail

### 3.1 Why there is no cursor

The two existing providers both sync from a *native incremental API*: gmail
historyId, tada day-walk — so each needs a cursor (file-based today). RSS has
none: every poll returns "the current window of items" (typically the last
10–25). The correctness mechanism is identical to tada/gmail regardless:

```
done-set = SELECT json_extract(data,'$.entry_id')
           FROM item
           WHERE _type = 'Entry'
             AND json_extract(metadata,'$.source') = 'rss'
```

built once per sync (never materialized as facts — the 700 MB lesson is
recorded in the gmail provider). A feed entry seen before is skipped; an entry
evicted from the feed window and re-served months later is still skipped
(the done-set persists in memogarden, unlike a feed-reader cache). A run that
dies mid-sweep self-heals: yielded facts are committed per-fact by the runtime
(`provider_runtime.py:274-284`), and the next sweep re-delivers only what the
done-set lacks.

### 3.2 Subscriptions as facts (OPML seed path)

OPML import creates one `Subscription` fact per outline (`@type='rss'` /
`@xmlUrl`), preserving nested folder structure as `categories`. After import,
the subscription list is *queryable memogarden content* — the provider reads
it at sync start, and `list subscriptions` / Studio could render it later. No
config file, no registry rows for "what I read": that is real-world
information, so per the user's principle it goes into memogarden. Re-importing
the same OPML is idempotent (dedup on `data.xml_url`).

### 3.3 The only true state: the provider-managed feed-state store — DECIDED app-internal

One entry per feed in a single provider-owned file,
`~/.local/share/memogarden/rss-feed-state.json` (gmail `historyId` file
conventions: written via `.tmp` + atomic rename, saved incrementally after
each feed is processed so a crash keeps durable per-feed progress):

```json
{"<xml_url>": {
  "fetch_url": null,            // adopted stable 301 target (see §7.4)
  "etag": null,                 // verbatim bytes incl. W/ and quotes
  "last_modified": null,        // verbatim header string, never normalized
  "body_hash": null,            // SHA-256 of decompressed response body
  "interval_s": 3600,           // adaptive, clamped [3600, 86400]
  "next_due_at": null,          // per-feed politeness scheduler
  "consecutive_errors": 0,      // transport streak
  "parse_errors": 0,            // parse streak (counted separately)
  "last_status": null,          // last HTTP status seen
  "last_error": null,
  "status": "active"            // active | gone (410) | dead (tombstone) | paused
}}
```

No memogarden migration, no fact churn. Trade-off accepted: per-feed health
is not SQL-queryable from memogarden (a CLI report over the JSON covers it);
promote to a `feed_states` registry table (RFC-004 infra tier) only if that
visibility ever becomes load-bearing.

---

## 4. Data model

New fact types follow the tada workflow: `schemas/feed.yaml` +
`schemas/types/facts/*.schema.json` + bundled copies in
`memogarden-system/system/schemas/types/facts/`. No DB migration needed for
fact types (`item._type` is a free string).

### 4.1 `Subscription`

```yaml
type: Subscription
data:
  xml_url:      # required — the feed URL; dedup key + identity anchor
  title:        # feed <title> / OPML text, when known
  html_url:     # site URL (OPML htmlUrl / feed link rel=alternate)
  categories: [] # OPML folder path
metadata:
  source: "opml"            # or "manual"
  imported_at:              # provenance of the seed
  feed_key:                 # short hash of xml_url — goes in our User-Agent
```
`canonical_at` = import time. Note: created via the operator's API (client CLI
verb), not by the provider — operator-owned data; scope labeling for
manually-created facts follows the existing API behavior (check at
implementation; likely unscoped, which is right — it's the user's own list).

### 4.2 `Entry`

```yaml
type: Entry
data:                       # ALL fields first-seen frozen → hash-stable
  entry_id:                 # required — identity chain result (§4.3)
  title:
  link:                     # entry permalink (resolved absolute)
  published:                # ISO 8601 UTC, from pubDate/updated/published
  author:
  description:              # feed-provided content, verbatim
  content_type:             # text/plain | text/html
  enclosures:               # [{url, type, length}] — pointers only, never fetched
metadata:
  source: "rss"
  feed:                     # Subscription.xml_url — RFC-012-style attribution;
                            # scope rules can key on this (RFC-012 note)
  feed_title:
  bozo:                     # feedparser leniency flag, when true
```
`canonical_at` = entry published time (the honest "when it happened").
`realized_at` = fetch time. First-seen wins for the `Entry` fact itself
(immutable, hash-stable). **Revisions are recorded — DECIDED 2026-11-16**:
when a feed re-serves a known `entry_id` with a newer `updated` *and* a
different content hash, the sweep yields a revision fact — Note-typed,
`contained_by` → Entry, `data {"description": <new content>, "revised_at":
<updated>}`, `metadata {source: "rss", feed, entry_id}` (tada Note pattern).
Timestamp-only touches (same content hash) are ignored — feeds that bump
`updated` on republish churn nothing. Revision notes dedup on
`(entry_id, revised_at)` via a second done-set projection (tada's separate
Task/Note sets — self-repairing after a mid-run crash).

Dedup key: `(metadata.feed, data.entry_id)` — feed-scoped because guids are
only unique per feed (syndicated feeds share guids).

### 4.3 Identity chain (spec-compliant, robust to broken feeds)

1. RSS 2.0 `<guid>` — compared as an opaque string ("Aggregators must view
   them as a string", RSS Board spec). If `isPermaLink` (default true), it is
   a URL — compare, **never fetch or resolve** it.
2. Atom `<id>` — REQUIRED per RFC 4287 §4.2.6, char-by-char comparison,
   never dereferenced.
3. RSS 1.0 `rdf:about`.
4. Normalized item link (scheme/host lowercased, fragment stripped).
5. Last resort: hash of (title + parsed published) — flagged unstable;
   CMSes that regenerate guids are the known dedup hazard (WordPress's own
   guidance: "the URL can change, but the GUID should never change").

Parsing uses **feedparser** (the Pilgrim/kurtmckee lenient parser: `bozo` bit,
RFC 3023 charset fallback chain, RFC 822 dates incl. obsolete zones and
2-digit years, RFC 3339/W3CDTF). Pure-Python + sgmllib3k; verify Python 3.14
compatibility in the server venv at implementation time. Never trust document
order — sort by parsed date for display, tiebreak on `entry_id`.

### 4.4 Relations

`Entry --contained_by--> Subscription` via `create_relations`, resolving the
Subscription by `json_extract(metadata,'$.feed')` — the tada Note→Task
pattern verbatim (`providers/tada-importer/tada_importer/provider.py:110-146`).
Revision notes and archived artifacts use the same `contained_by` verb,
targeting the Entry / Subscription respectively.

### 4.5 Archived artifacts — DECIDED 2026-11-16

Two artifact classes land in Soil alongside the Entry facts:

1. **Raw feed XML** — each 200 response that delivered new entries is
   archived verbatim as a content-path fact (existing `Attachment` pattern:
   bytes in the content-addressed store, fact data
   `{filename, mime_type, size, sha256, title, content_path}`,
   `contained_by` → Subscription). Provenance: "as served by the publisher
   at time T" — the gmail full-email analogue. Responses that produced
   nothing new (304s, repeat 200s) are not archived.
2. **Full-fetched articles** — when a feed is fetched beyond its summary, the
   article content is a **separate document fact** linked `contained_by` →
   Entry, never merged into `Entry.data`. Type naming (`Attachment` vs a new
   `Document` type) is settled at schema time alongside the sessions-provider's
   identical open question; the storage pattern is the same either way.

**Fetch policy for class 2** (politeness guard): default OFF for every feed;
the operator opts feeds in (truncated-summary feeds are the use case). An
opted-in feed gets at most one page fetch per *new* entry, never re-fetches
already-fetched articles, and page fetches share the sweep's wall-clock
budget. User-initiated fetches (read-time) are a different politeness class
and always allowed. This stays clear of the rachelbythebay TTRSS anti-pattern
(re-crawling every linked post on every poll): opt-in, new-entries-only,
fetched-once.

---

## 5. Politeness policy (the concrete rules)

### 5.1 Scheduling

- **Default interval: 1 h per feed** — the canonical personal-reader cadence
  (Google Feedfetcher: "shouldn't retrieve feeds from most sites more than
  once every hour on average"; Bloglines 2005: hourly; Miniflux default 60;
  FreshRSS default hourly). Our heartbeat executor already fires hourly
  (`OnCalendar=hourly`, `AccuracySec=5min`), so the infrastructure *is* the
  floor — the provider cannot over-poll even if it tried.
- **Per-feed `next_due_at`** in the provider's feed-state store (§3.3) — feeds whose due time hasn't
  passed are skipped by a sweep (this is how backoff and adaptive intervals
  work without extra runtime machinery).
- **Adaptive interval** (simple, v1): decaying average of observed inter-item
  interval, updated only on 200s with new entries; effective interval =
  clamp(max(1 h, observed), 1 h, 24 h); change only after ≥3 observations.
  This answers rachelbythebay's fixed-interval complaint: a monthly blog gets
  polled daily, not hourly. (Miniflux `entry_frequency` and NewsBlur tiers are
  the production precedents.)
- **Feed metadata hints, honored as floors, never as mandates** (RSS Board
  Profile: treat `ttl` "as a suggestion, not a hard rule"; <1% of feeds use
  skipHours):
  - `<ttl>` minutes and `Cache-Control: max-age`: raise the effective
    interval (never lower it below our 1 h base); cap 24 h.
  - `<skipHours>`/`<skipDays>` (hours are **GMT** per the spec; accept 0 and
    24 as midnight): if a feed's slot lands in a skipped hour, defer to the
    next sweep; never let a skip delay a feed > 25 h.
  - RSS 1.0 `sy:updatePeriod/updateFrequency`: same floor treatment.
  - Atom has no hints (RFC 4287) — adaptive logic covers it.
- **No thundering herd**: sweeps are serial (§7.2), so requests are naturally
  spread and ordered; per-feed next-due offsets are jittered
  (AWS full-jitter result: without jitter, retries synchronize).

### 5.2 Conditional GET (the single most important rule)

- Store `etag` **verbatim** (opaque bytes incl. `W/` weak prefix and quotes —
  RFC 9110 §8.8.3) and `last_modified` **verbatim** (never normalize the
  HTTP-date). Send `If-None-Match` and/or `If-Modified-Since` on every poll
  once known; both, when both are offered (RFC 9111 §4.3.1: ETag→INM is a
  MUST, LM→IMS a SHOULD; feedparser docs: "some servers support one but not
  the other").
- **Update stored validators from every response** — including from a 304
  (RFC 9111 §4.3.4: a 304 may refresh validators for unchanged content; that
  is intended, not a bug), and *before* any body-hash early-return. This is
  the NetNewsWire bug (saved new conditional-GET headers only after a hash
  early-return → never picked up changes; fixed Aug 2024)
  https://inessential.com/2024/08/03/netnewswire_and_conditional_get_issues.html
  and the rachelbythebay degenerate-loop finding (readers that only update
  the ETag when Last-Modified changed decayed into permanent unconditional
  fetches) https://web.archive.org/web/20240612012507/https://rachelbythebay.com/w/2024/06/11/fsr/
- **Independent validator updates**: etag and last_modified update
  independently; never gate one on the other.
- **Body-hash fallback**: when no validator is offered, skip parsing when the
  SHA-256 of the decompressed body is unchanged.
- **Adversarial-validator defense**: after 3 consecutive "200, same
  body-hash, new etag", freeze the etag (stop sending INM for that feed) and
  rely on the body hash — mirrors the deliberate pathological tests
  rachelbythebay ran (NetNewsWire and Emacs Elfeed passed them, Mar 2025).

### 5.3 Error handling table

Base = current effective interval; all computed delays get **full jitter**
`random(0.5, 1.0) × delay` (AWS/Brooker), capped at 7 days.

| Class | Immediate action | Escalation | Terminal |
|---|---|---|---|
| **410 Gone** (RFC 9110 §15.5.11: likely permanent, publisher's opt-out) | mark `status='gone'`, stop polling | — | yes — user-visible in state report; data retained |
| **404** (RFC 9110 §15.5.5: "does not indicate whether … temporary or permanent") | ×4 | streak 5 → ×24; streak 20 or 30 days continuous → `status='dead'` | weekly tombstone probe; never auto-delete |
| **429** (RFC 6585) | honor `Retry-After` (parse seconds **and** HTTP-date forms, RFC 9110 §10.2.3); no header → ≥60 min (NewsBlur precedent) | repeat: max(Retry-After, previous×2) | reset only on a 200 |
| **503 / other 5xx** | honor `Retry-After`; else ×2 | ×4, ×8 … | cap 7 d |
| Timeout / DNS / TLS / refused | ×2, **per-host circuit breaker**: skip the host's remaining feeds this sweep | ×4, ×8 … | host-level errors never count toward feed-death streaks (distinguish "feed broken" from "site down") |
| Parse error (200 + bozo/unparseable) | ×2, separate parse streak | streak 3 → pause auto-poll, flag for attention (Miniflux `POLLING_PARSING_ERROR_LIMIT=3`) | resume on user action / feed fix |
| **301/308** | follow (≤5 hops); after 3 consistent targets, adopt into `feed_states.fetch_url` (§7.4) | loop → treat as 5xx | — |

Never ignore a 429/Retry-After: rachelbythebay's server now auto-blocks hosts
after ~5 ignored 429s — rudeness converts to total loss of access
(https://web.archive.org/web/20241218155851/https://rachelbythebay.com/w/2024/12/17/packets/).

### 5.4 Identification

```
User-Agent: MemoGarden-RSS/0.1 (+https://memogarden.ngjunsiang.me/privacy/;
           1 subscriber; feed-key: <8-hex hash of xml_url>)
Accept: application/atom+xml, application/rdf+xml, application/rss+xml,
        application/xml;q=0.9, text/xml;q=0.2, */*;q=0.1
Accept-Encoding: gzip, deflate
```

- Product + version + contact URL + honest subscriber count (1). Precedents:
  Feedbin (`feed-id:… - N subscribers`, held up as the model citizen in the
  largest UA survey, jefftk.com/p/looking-at-rss-user-agents); Google
  Feedfetcher's `N subscribers; feed-id=…`. feedparser ships **no UA by
  default** and asks embedders to set one — we must not be a no-UA client.
- `feed-key` lets a publisher correspond about abuse (rachelbythebay's
  tagged-feed premise; she identified and named every offender via it).
  Note: this publishes the privacy-page URL to every polled server — inherent
  to polite identification; the page is already public.
- **robots.txt: not consulted** — feeds are explicitly published and
  subscriber-chosen; Google Feedfetcher ignores robots.txt for exactly this
  reason and directs opt-outs to 404/410, which we honor as the real
  opt-out channels. Flagged controversy; our hourly+backoff cadence is far
  below any plausible Crawl-delay.

### 5.5 Push (WebSub / RSS Cloud): rejected for v1

WebSub is a W3C Rec (2018) with real hub infrastructure, but: it needs an
inbound HTTPS endpoint, a lease-renewal state machine (10-day default leases),
and hubs are fragile (pubsubhubbub.appspot.com 503 waves; Superfeedr is
commercial). With conditional GET done right, an hourly poll is nearly free —
push buys latency we don't need. Parse and record `Link rel=hub` at fetch time
for a future opt-in. RSS Cloud is defunct.

---

## 6. rachelbythebay dossier (offline site; verified Wayback captures)

The single best external source on how feed readers actually behave. She ran a
"Feed Reader Behavior Project": per-installation tagged feed URLs on a
Raspberry Pi, logging and scoring every fetcher; ~70k requests across 97 keys.
Project page: https://rachelbythebay.com/frb/ (archived). Site went dark
~mid-Nov 2026 (A records removed; HN thread 50487435).

| Post (date) | Wayback | Lesson for us |
|---|---|---|
| Two months of feed reader behavior analysis (2024-08-02) | [capture](https://web.archive.org/web/20240803041137/https://rachelbythebay.com/w/2024/08/02/fs/) | Scorecard naming names: Feedly "just abusive" (unconditional 30-min multi-host polls); TTRSS/Friendica never send conditionals; Netvibes polls with month-old validators. Honor roll: Miniflux, rawdog, walrss, CommaFeed, Vienna. |
| So many feed readers, so many bizarre behaviors (2024-05-27) | [capture](https://web.archive.org/web/20240528065156/https://rachelbythebay.com/w/2024/05/27/feed/) | Hard-coded 2-min poller kept hitting a wall of 403s; clients sending If-Modified-Since derived from the feed's `<updated>` element (mixing feed-time and HTTP-time — never do this); ignored Retry-After; URL-guessers ignoring `<link rel=alternate>` discovery. |
| Feed readers which don't take 'no' for an answer (2024-12-17) | [capture](https://web.archive.org/web/20241218155851/https://rachelbythebay.com/w/2024/12/17/packets/) | Unconditional 502 KB fetch every 20 min against 429 + Retry-After: 86400 — ~36 MB/day of identical bytes per client; her server auto-bans after ~5 ignored 429s. |
| Words fail me sometimes when it comes to feed readers (2024-12-10) | [capture](https://web.archive.org/web/20241211082223/https://rachelbythebay.com/w/2024/12/10/feed/) | NetNewsWire fired ~45 unconditional requests at 4-5/s for 11 s — at her post *index*, not the feed. Burst + wrong URL + no conditionals = worst-of-all-worlds. |
| Attack of the broken feed reader (2018-04-12) | [capture](https://web.archive.org/web/20180413073124/https://rachelbythebay.com/w/2018/04/12/badfeed/) | TTRSS crawled every post page after fetching a full-content feed (101 req/17 s). Basis for the URL-vs-full-page answer (§2). |
| Summarizing the last 45 days of feed reader behaviors (2025-03-03) | [capture](https://web.archive.org/web/20250327180422/https://rachelbythebay.com/w/2025/03/03/feed/) | Adversarial validator tests; double-tap on feed add (Yarr: again 300 ms later); NextCloud News sent an If-Modified-Since it was never given; fake-Chrome UA with broken HEADs. |
| Some early results for feed reader behavior monitoring (2024-06-11) | [capture](https://web.archive.org/web/20240612012507/https://rachelbythebay.com/w/2024/06/11/fsr/) | Readers that update the ETag only when Last-Modified changed decayed into permanent unconditional fetches → independent validator updates (§5.2). Startup volleys <1 s apart (Liferea, NewsBlur, FreshRSS, Thunderbird ext). |
| Leaking URLs to the clown (2024-06-24) | [capture](https://web.archive.org/web/20240624200733/https://rachelbythebay.com/w/2024/06/24/feed/) | A reader secretly proxied fetches via a vendor cloud, which polled her unique URL forever after uninstall. Our answer: self-hosted, fetch direct, no third-party proxy. |
| Feedfetcher is still polling as if nothing happened (2013-07-12) + Pull the plug already (2013-08-20) | [capture 1](https://web.archive.org/web/20231108004911/https://rachelbythebay.com/w/2013/07/12/reader/) · [capture 2](https://web.archive.org/web/20231108004240/https://rachelbythebay.com/w/2013/08/20/reader/) | Zombie aggregators polled her feed for months after Google Reader died. If we ever retire this provider, deregister it — don't leave a heartbeat pointing at the world. |
| The feed reader score service is now online (2024-05-30) + spin (2024-05-29) | [capture](https://web.archive.org/web/20240531045654/https://rachelbythebay.com/w/2024/05/30/fs/) | Methodology (unique per-user URLs, full request logging) + first score: 34 requests, 0 good conditionals, ~25 MB/day wasted by one installation. |

**Distilled anti-pattern checklist** (each traced above; our countermeasures
in §5): never fetch unconditionally after the first request; never invent or
derive validators (only verbatim echoes of what the server sent); never reuse
stale validators (update from every response incl. 304); never ignore
429/503/Retry-After; never keep polling a 403/404/410 feed (streak →
tombstone); never poll on a fixed rate unrelated to the feed's change rate
(adaptive interval); never burst (serial sweeps, jitter, no double-tap on
add); never crawl pages a full-content feed already gave you; never mangle the
configured URL (no path-gluing, no trailing-space 404 loops — trim whitespace
on OPML import); never fetch through a third-party proxy; never omit or fake
the User-Agent; and when the reader itself is retired, stop the heartbeat.

---

## 7. Architecture fit

### 7.1 Provider contract shape (verified against the live runtime)

```python
class RssProvider:
    def __init__(self, config: dict): ...          # defaults merged over registry config
    def sync(self, since=None) -> Iterator[dict]:  # runtime's `since` accepted, ignored
        yield {"type": "Entry", "data": ..., "metadata": ...,
               "canonical_at": ..., "realized_at": ...}
    def create_relations(self, fact): ...          # Entry --contained_by--> Subscription
```

The runtime (`memogarden-api/api/provider_runtime.py:255-306`) inserts each
yielded fact, applies the operator's scope policy (providers cannot forget,
SG-7), commits per fact, then calls `create_relations`. One exception out of
`sync()` marks the whole run failed (facts already yielded stay committed);
there is **no partial-success status**, so per-feed failures must be swallowed
inside the generator (gmail `_fetch_batch` pattern) and recorded in
`feed_states` — raise only if the sweep failed wholesale.

### 7.2 Scheduling model: one provider-level run, serial sweep

Each heartbeat: `run_all_due` starts the provider's single run; the provider
(1) reads Subscription facts + done-set projection, (2) reads its feed-state
store (§3.3), (3) serially fetches feeds with `next_due_at <= now`, each with
a bounded socket timeout (default 15 s) and a per-run wall-clock budget
(default ~20 min) checked between feeds — over-budget feeds are simply
*deferred* (left due), never failed (gmail backfill's "hit the budget, defer
rather than fail" precedent). Then persists the state store and yields new
Entries as it goes.

**Why not account-per-feed** (registering each feed as an RFC-012 account,
which would give per-feed intervals/locks/needs_reauth parking from the
runtime)? It would work, but: 200+ "accounts" per provider abuses the
credential-subject semantics (`provider_accounts` rows with null
credential_refs, `verify_account` ceremony), parallel per-account triggering
loses serial pacing and per-host grouping, and OPML import would need 200
account registrations. The provider-managed state store (§3.3) gives the same per-feed
scheduling with zero migrations and none of the semantic stretch. Revisit only
if per-feed isolation ever becomes real (e.g. authenticated feeds).

**The one runtime hazard this design must respect**: provider runs are daemon
threads with **no wall-clock timeout and no kill** — a hung sync holds the
single-flight lock until server restart (`cleanup_orphaned_runs` only runs at
startup). Hence the mandatory per-request socket timeouts + sweep budget; this
is the first house provider whose upstream is hundreds of arbitrary external
hosts, so the boundary work (timeouts, budget, per-host breaker) is not
optional.

### 7.3 Scope policy

No `PUT /providers/rss/scope-policy` needed initially: with no policy row, the
ratified fail-closed catch-all labels every Entry **triage** — correct, since
feed content is untrusted third-party material (same posture as the 279
triage-labeled emails). Later, per-feed rules can key on
`metadata.feed` (RFC-012 note: scope rules can key on `metadata.account` —
same mechanism). Read-path enforcement is still OPEN (RFC-011 item 5), so
labels are advisory until then, as elsewhere.

### 7.4 Redirects vs identity

`Subscription.data.xml_url` is the permanent identity anchor (dedup key,
`metadata.feed`, done-set scoping). On 301/308 we always fetch the target,
and after 3 consistent targets adopt it into the state store's `fetch_url` — but
never rewrite the subscription identity, because changing `metadata.feed`
would orphan the done-set keys of every stored Entry (they'd need a metadata
re-stamp sweep). One extra 301 per poll for redirecting feeds is the cheap,
honest behavior.

### 7.5 OPML import

New client verb following the verb-first grammar: `memogarden import opml
--file feeds.opml` — parses outlines (trimming whitespace from `xmlUrl` — the
trailing-space 404 loop in her logs is a real failure mode), creates
`Subscription` facts via the API (idempotent on `xml_url`), and prints a
per-feed summary. Nested folders → `categories`.

### 7.6 Install

Package `providers/rss-importer/` (Poetry; deps: `memogarden-system` path dep
+ `feedparser`). Live `MEMOGARDEN_PROVIDER_ROOTS` already includes
`$REPO_ROOT/providers` (install-user-service.sh:84), so after restart:
`memogarden register provider --name rss --module rss_importer.provider
--class RssProvider --interval 3600`. Deploy = git pull + `systemctl --user
restart memogarden` (no migration — the state store is provider-local).

---

## 8. Decisions — recorded 2026-11-16

1. **State store → app-internal.** Raw feed data "isn't information for
   memogarden (that's only after processing)": the provider tracks each feed
   in its own state store (§3.3, gmail-file precedent) and passes new URLs +
   metadata to memogarden as they arrive. Subscriptions stay as facts (the
   reading list is real-world info). Promotion to a queryable registry table
   remains open if per-feed health reporting is ever wanted.
2. **Feed responses → archived in Soil.** Raw XML archived per
   new-entry-bearing response (content-path fact, `contained_by` the
   Subscription); full-fetched articles stored as **separate documents**
   linked to the Entry (§4.5). Fetch policy default: opt-in per feed,
   new-entries-only, fetched-once — flippable per feed anytime.
3. **Entry revisions → recorded.** A known `entry_id` re-served with newer
   `updated` AND a different content hash yields a revision Note fact (§4.2);
   timestamp-only touches ignored.
4. **UA contact → customary form.** Custom is `Product/version
   (+https://explanatory-URL)` — contact details live on the page, not in
   the UA string (Feedfetcher's feedfetcher.html, Miniflux's +miniflux.app;
   email-in-UA is not customary). Shipped UA: privacy URL + `1 subscriber` +
   `feed-key`. Optional refinement: a small fetcher-explainer page on
   memogarden.ngjunsiang.me (the truly customary pattern) — nice-to-have.
5. **Per-feed scope rules → deferred** (default applied, no PUT needed): the
   ratified fail-closed catch-all labels every Entry triage from day one;
   rules keying on `metadata.feed` gain meaning when RFC-011 item 5
   (read-path enforcement) lands.

## 9. Implementation sequence

1. `schemas/feed.yaml` + JSON-schema mirrors (Subscription, Entry) + bundled
   copies; decide Attachment-vs-Document naming for archived artifacts
   (align with the sessions-provider open question)
2. Provider package: fetcher (conditional GET, backoff table, host breaker),
   translate (feedparser → facts + archived artifacts), feed-state store
   (§3.3), sweep loop; tests mirroring `providers/tada-importer/tests/`
   (wire protocol vs a local HTTP server; 304/adversarial-validator/
   Retry-After cases; done-set semantics vs real tmp Soil)
3. `memogarden import opml` verb (client repo)
4. Register + restart; user seeds OPML; first sweep (expect a few thousand
   Entry facts from the initial windows + one archived XML per feed)
5. Docs: `docs/api.md` providers section + provider README

## 10. Sources

Normative: RFC 9110 §8.8.3/§10.2.3/§13.1.2-3/§15.4.5/§15.5.5/§15.5.11/§15.6.4,
RFC 9111 §4.3.1/§4.3.4/§5.2.2.1, RFC 6585 §4, RFC 4287 §3.3/§4.2.6, RSS 2.0
specification + Best Practices Profile (rssboard.org), RSS 1.0 syndication
module (archived), WebSub W3C REC (2018-01-23).
Engineering: feedparser docs (http-etag, http-useragent, bozo,
character-encoding, date-parsing), Google Feedfetcher docs, Miniflux docs +
source, NewsBlur source (`feed_fetcher.py`, `models.py`), FreshRSS docs,
Feedbin subscriber stats, Brent Simmons inessential.com 2024-08-03,
jefftk.com UA survey, AWS exponential-backoff-and-jitter.
rachelbythebay: posts in §6 (all via verified Wayback captures) + Feed Reader
Behavior Project + HN 50487435.
Codebase: survey of `memogarden-api/api/provider_runtime.py`,
`provider_registry.py`, `memogarden-system/system/schemas/sql/{core,soil}.sql`,
`providers/{tada-importer,gmail-importer,mbox-importer}`, `schemas/task.yaml`,
`plan/rfc_004…/011…/012…`, `scripts/install-user-service.sh` (line refs in §3/§4/§7).
