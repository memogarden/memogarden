# RFC-014: Google Calendar Provider (gcal) — v1 DRAFT

**Status**: Phase A IMPLEMENTED on `feat/gcal-provider` (system + api + root worktrees; suites green; pending review/merge). Decisions all settled (AUQ 2026-11-16, §14).
**Related**: RFC-004 §12.2 (provider runtime), RFC-010 (agent identity), RFC-011 (scopes/grants), RFC-012 (provider accounts; §10 reserved exactly this capability widening), PRD v0.11 §Provider Plugin Interface.

---

## 1. Summary

A Google Calendar read-only ingestion provider (`gcal`) that mirrors each concrete calendar event into a **Core Event entity** (mutable belief — the schedule as the user believes it), and creates Soil facts only when a review action marks an event as occurred. The provider extends the runtime contract so providers can yield **entity upserts** — gcal is the first Core-writing provider, and the contract extension is designed so future Core-writing providers inherit it.

Blocker decisions (AUQ-settled 2026-11-16): **contract extension** for Core writes (not direct writes); **operators + agents** may mark occurred; **full-history backfill**; **entity-stamp-only** removals. Minor decisions (§14): fact kind `EventOccurrence`; owner-role calendars by default; **hourly** cadence; **triage** scope default.

## 2. Model: Event is a Core entity

Per the platform principle "Mutable Snapshot, Immutable Memory": the schedule is a mutable belief; occurrence is memory.

- **Event = Core entity** (`core_` uuid), never a Soil fact by itself.
- **Events become Facts only after they have occurred** — and only via explicit review (`POST /events/{uuid}/mark-occurred`), **never silently when the date passes** (D1/D3).
- **Events are the beliefs; observed/experienced events are the fact stream** — the marking-time fact (§6.3) is a member of that stream.
- gcal would be the **first provider writing Core entities**; the contract extension (§3) is the enabling mechanism.

### 2.1 Entity definition

`data` — source-agnostic belief fields (authoritative spec: `schemas/event.yaml`, drafted alongside this RFC):

| Field | Req | Type | Notes |
|---|---|---|---|
| `source_id` | ✓ | string | Native source id. Per-calendar uniqueness per Google docs ("collisions aren't guaranteed detectable" across calendars) — hence the natural key carries `calendar_id` (§2.2). |
| `title` / `description` / `location` | – | string | Untitled/description-less events exist; validation stays minimal (gmail lesson: over-strict validation permanently excludes immutable facts). |
| `starts_at` / `ends_at` | ✓/– | datetime-UTC **or** bare date `YYYY-MM-DD` | Date-only = time ambiguity, naturally "all-day" (D5 — no `all_day` bool). **`ends_at` is exclusive** (RFC 5545 DTEND convention: a one-day date-only event ends the *next day*). Timed events UTC-converted (tada precedent); date-only values carry no timezone (floating local date). |
| `occurred_at` | – | datetime-UTC | **Null = has not happened / did not happen.** Providers never set it; date passage never sets it; set ONLY by mark-occurred review (D3). |
| `status` | ✓ | enum `confirmed / tentative` | Source truth of the schedule. `cancelled` is never stored as an Event — removal is stamped on the entity (D12). |
| `participants` | – | [string] | Plain identifiers — email preferred, displayName/id fallback (D6: no Person entity yet). Capped (`participants_max`, default 50) with `metadata.attendee_count` preserved. |
| `organizer` | – | string | Email preferred. |

`metadata` — source-specific:

| Field | Req | Notes |
|---|---|---|
| `source` | ✓ | enum `google_calendar` (enum grows with providers) |
| `account` | ✓ | RFC-012 INV-7 attribution; scope-rule matchable |
| `calendar_id` / `calendar_summary` | ✓/– | Scope-rule matchable; part of the natural key (§2.2) |
| `event_id` / `ical_uid` | ✓/– | Source ids; `iCalUID` = cross-system identifier |
| `etag` + `updated_at` | ✓ | Etag map drives upsert no-op detection (§4.4) |
| `recurring_event_id` | – | NATIVE link to the source pattern (never dangles — tada `follows_from` precedent). RRULE *text* capture deferred (§12); instances omit recurrence lines. |
| `original_start_at` | – | Where this instance sat in the series before a move |
| `captured_after_fact` | – | Seeded history: the calendar asserted it, we never live-observed it. Review triage marker (§7). |
| `source_status` / `removed_at_source` / `detection_source` | – | Removal stamps (D12), set only by the provider/reviewer-agnostic stamping path (§4.5) |

### 2.2 Natural key + etag map

Natural key: `(source, account, calendar_id, source_id)` — per-calendar uniqueness (D8/D12). Each run projects an etag map from the corpus (SQL `json_extract` projection filtered by `metadata.account` — the 700 MB lesson): etag unchanged ⇒ no-op; changed ⇒ entity update; unknown key ⇒ create. Derived per run; deleting state merely forces rebuild.

## 3. Provider contract extension: entity upserts

gcal is the first Core-writing provider; the extension is designed for inheritance by future Core-writing providers.

### 3.1 Yield shape

Providers yield dicts of a new form alongside (not mixed with) fact yields in a single `sync()`:

```python
{
    "op": "upsert_entity",
    "type": "Event",                      # CamelCase entity type
    "key": {                              # natural key; runtime resolves entity uuid
        "source": "google_calendar",
        "account": subject,
        "calendar_id": cid,
        "source_id": sid,
    },
    "data": {...},                        # generic belief fields
    "metadata": {...},                    # provider metadata — references, never secrets (RFC-004)
}
```

- Providers **yield**; the runtime owns storage: natural-key lookup → create/update/no-op, scope-policy application, delta logging, per-item commit — one seam, consistent with "providers yield, MemoGarden stores".
- Fact yields (existing shape) stay unchanged; the runtime dispatches on the presence of `op` (no `op` ⇒ legacy fact path).

### 3.2 Runtime seam (system + api)

1. Natural-key lookup: `find_entity_by_source_key(type, key)` — projected `json_extract` scan (a system-side helper; at calendar volumes a scan is fine; index optional later).
2. **Etag no-op rule**: stored `etag == incoming` AND `source_status` unchanged ⇒ skip (no write, no delta). Keeps hourly no-op runs cheap and the delta stream signal, not noise.
3. **Storage mapping**: Core entities have a single JSON `data` column (no metadata column) — yielded `data` + `metadata` merge flat into `entity.data` (collision = run failure); scope labels ride `data.scopes` (§10).
4. Create: `core_` uuid via `core.entity.create`, scope-labeled, committed per item.
5. Update: **merge semantics** — incoming top-level keys overwrite, absent keys keep stored values (explicit null clears). Google resources arrive complete on change, so merge is faithful; the merge exists for tombstones, whose payloads guarantee only ids and must not clobber stored beliefs (§4.5). Stored runtime labels don't survive the merge — the live policy re-labels the merged result, so identical labels produce no delta noise.
6. **EntityDelta authored by the runtime seam** (§12 item 2 resolved: nothing in the platform wrote EntityDelta facts before — the consistency checker expected them, so the seam now writes them): one fact per update, `{entity_uuid (core_-prefixed, per the entitydelta schema), entity_id (plain, mirrored for the RFC-008 orphan checker), entity_type, commit (new hash), parent (prev hash), ops.changed {field: {from, to}}}`. **Core commits before Soil** per item — a crash between commits can lose a delta (recovered on the next upsert) but can never orphan one.
7. Per-item commit (attachments-pass lesson: short transactions interleave under the 5s busy_timeout).
8. Capabilities: registration validates the optional class attribute `ENTITY_UPSERTS = True` → capability `upsert_entities`; the runtime refuses entity yields from providers that don't declare it (defense in depth). Fact-only providers unaffected (the Core connection opens lazily — a fact-only run pays nothing).

## 4. Ingestion engine

### 4.1 Calendar selection

Default (settled): calendars where `accessRole == "owner"`; config `calendar_ids` allowlist overrides; `list-calendars` CLI to discover. Subscribed calendars (holidays, birthdays, week numbers) are excluded by default.

### 4.2 Two-stage sync (RFC-012 §4.10 discipline)

**Incremental always first.** Per configured calendar with a stored token: `events.list(syncToken, singleEvents=true)` — `singleEvents` is permitted with `syncToken` per the events.list reference (forbidden list: `iCalUID, orderBy, q, privateExtendedProperty, sharedExtendedProperty, timeMin, timeMax, updatedMin`), and incremental calls must reuse the identical parameter set as the initial sync ("avoid undefined behavior"). Changed items → upsert path (§3); `status: cancelled` tombstones → removal stamps (§4.5). Token advances **only in the `finally` block on full generator consumption** (INV-8, gmail discipline). A burst larger than `batch_limit` leaves state untouched — the remainder re-delivers next run; the etag map absorbs it.

**Seed**: no token → full `events.list(singleEvents=true)` with **no `timeMin`** (full-history decision). Page-budgeted — defer, never fail; `nextSyncToken` stored only when the whole listing was walked (conservative completion). Events whose `ends_at` (or `starts_at`) already lies in the past at seed time get `metadata.captured_after_fact: true` (§7).

**410 GONE** (`fullSyncRequired`): clear that calendar's token → reseed → forced audit diff (§4.3).

### 4.3 Audit pass (load-bearing)

Monthly (config `audit_interval_days: 30`), plus forced after every reseed: windowed full listing (`singleEvents=true, timeMin=<horizon>`) diffed against the corpus per calendar:

- **Silent vanishings**: master deletion / RRULE shortening tombstone only the master id — which we never store — so instances vanish silently; caught only here.
- **Newly materialized instances**: an unchanged master produces no change entry, so instances entering Google's expansion horizon appear only via this diff.

### 4.4 Etag map / upsert flow

Per run and calendar, project `(calendar_id, source_id, etag)` for `metadata.source = 'google_calendar'` entities (projected SQL, filtered `metadata.account = :subject`). Per changed item: key lookup → no-op on same etag; update on changed etag (delta stream logs it); create on unknown key.

### 4.5 Removal stamps (D12)

Tombstone for a known key → stamp entity metadata: `source_status: "cancelled"`, `removed_at_source: <now>`, `detection_source: "sync_tombstone" | "audit_diff"` — one delta per stamping update. Tombstone for a never-stored key → ignore. The delta stream is the immutable removal log; no `SystemEvent` facts, no tombstone-re-delivery dedup burden.

## 5. Auth & credential sharing

- gcal reuses `google:oauth-client` and `google:{subject}` HACM entries (RFC-012 §3 names this exact case). Widen `GOOGLE_SCOPES` in `gmail-oauth/__init__.py` with `https://www.googleapis.com/auth/calendar.readonly`, re-consent once; the refreshed token updates `google:{subject}` in place (409-on-register = re-link semantics).
- `calendar.readonly` is a *sensitive* (not *restricted*) scope — the published app may show an unverified-scope warning at consent; acceptable at personal scale (RFC-012 §7 posture).
- **Footgun**: revoking `google:{subject}` kills gmail + gcal together — the CLI must warn.
- `verify_account`: the account's **primary calendar's `id` in `calendarList` equals the account email** — subject cross-check with no extra scope (INV-3 discipline, no Gmail profile call needed).

## 6. Mark-occurred pipeline

The belief-revision action. Never automatic; date passage does nothing (D3).

### 6.1 Endpoint

`POST /api/v1/events/{uuid}/mark-occurred` with body `{"occurred_at": "<optional ISO datetime, defaults to now>"}`.

### 6.2 Authz

- **Operator JWT** — always allowed (belief revision is a human-judgment action by default).
- **Agent-bound API key** (RFC-010: key with `agent_entity_id` set) — allowed; marking is attributable.
- **Unbound API key** — 403 (unbound keys share the username frame, not separate principals — RFC-010 invariant).
- Every marking writes an **Action fact** (actor = operator or agent entity, target event, `occurred_at`) — full audit.

### 6.3 The EventOccurrence fact

Marking creates one immutable **EventOccurrence fact** in Soil:

```python
data: {
    "event_uuid": <core uuid>,            # linkage to the belief
    "occurred_at": <ISO datetime>,        # real-world ground truth, set by review
    "snapshot": {                         # event state at marking time
        "title", "starts_at", "ends_at", "location", "participants", "organizer", "status"
    },
}
metadata: { "source": "google_calendar", "account": ..., "calendar_id": ..., "source_id": ... }
canonical_at = occurred_at
```

Fact kind **`EventOccurrence`** (settled): "Event" stays the entity; the fact names the recorded occurrence, keeping prose and queries self-describing.

**Idempotency**: marking an already-marked event returns the existing EventOccurrence fact (200, no new fact, no duplicate Action).

## 7. Backfill & review posture (full-history decision)

- Seed imports **all** events Google still holds — potentially years — as pending-review entities (`occurred_at: null`).
- Past-at-seed events carry `captured_after_fact: true` so review can triage "scheduled, never live-observed" from live-captured upcoming events.
- No silent conversion: a date-rolling cron never touches `occurred_at`. The review backlog is deliberately explicit; batching lives with the (future) review UI / agent reconciliation.

## 8. Schema artifacts

- `schemas/event.yaml` — Core Event entity (drafted alongside this RFC, same conventions as task.yaml: source-agnostic `data`, provider `metadata`, natural key, canonical rules, example).
- Packaged entity JSON Schema under `memogarden-system/system/schemas/types/entities/` + root mirror — added in Phase A implementation (like task's fact schema precedent, documentation-first; `create_entity` does not runtime-gate on it).
- The `EventOccurrence` fact schema artifact rides with Phase B.

## 9. Configuration reference

| Key | Default | Notes |
|---|---|---|
| `calendar_ids` | `[]` → owner-role calendars | Allowlist overrides default |
| `batch_limit` | 500 | Upserts per run before deferral |
| `page_budget` | 40 | `events.list` pages per run (defer, never fail) |
| `participants_max` | 50 | Trim + `metadata.attendee_count` |
| `audit_interval_days` | 30 | Audit pass cadence; forced after reseed |
| `interval` | 3600 | Heartbeat cadence — hourly chosen explicitly (§14): freshness over KDF thrift; accepts 24 HACM Argon2id unlocks/day (~1 GiB transient each). Cheap no-op = 1 calendarList + N token calls. |

## 10. Scope policy posture

- Zero scope code in the provider (SG-7 — providers are never scope-gated; the runtime applies policy at the storage seam).
- Entity scoping: operator policy applies at the runtime seam; rules match the merged storage view; labels land in **`data.scopes`** (entity analogue of fact `metadata.scopes` — entities have no metadata column; the future entity read-path enforcement reads this). `metadata.account` + `metadata.calendar_id` rule-matchable (flat in storage).
- Scope posture (settled): **triage default** — gcal registers with no scope policy; the ratified fail-closed catch-all routes every unmatched event to `triage` (same live posture as gmail today). Per-calendar overrides stay available later via rules on `metadata.calendar_id`.
- **Dependency note**: entity read-path enforcement (RFC-011 punch-list item 5) is OPEN — fine while enforcement is advisory; scoped *reads* of entities (and removal stamps) complete with that punch-list item.

## 11. Phasing & deployment

- **Phase A** — contract extension (system + api), `schemas/event.yaml` + entity JSON schema, provider package (seed/incremental/audit/removal stamps), CLI (`auth-login --register`, `list-calendars`; the audit runs in-provider monthly + forced after reseed — a separate CLI audit trigger is deferred; manual runs go through `POST /providers/gcal/run`), tests. Live verify singleEvents+syncToken reliability (§12).
- **Phase B** — mark-occurred endpoint + `EventOccurrence` fact + Action attribution.
- **Deployment rhythm** (per repo conventions): merge system → api → root; `.pth` into server venv (tada pattern) or Poetry path dep; `memogarden register provider`; `auth-login --register` per Google account; operator decides scope-policy PUT; `systemctl --user restart memogarden`. Never restart for client-only changes.

## 12. Verify-at-implementation list

1. `singleEvents=true` + `syncToken` per-instance change delivery reliability (reference-permitted; field-proven folklore says it works — verify live in Phase A).
2. ~~Delta logging on entity updates~~ — **RESOLVED in Phase A**: nothing in the platform wrote EntityDelta facts (only the RFC-008 checker read them); the runtime seam now authors them per update, with core-first commit ordering so an orphan delta is impossible.
3. Google's instance expansion horizon behavior (undocumented bound; observe empirically).
4. Instance id stability + `recurringEventId` presence on instances.
5. >200-guest `responseStatus` non-propagation caveat impact.
6. Verify the primary-calendar-id subject cross-check (§5) against a live account.
7. Pre-existing (observed during Phase A, NOT introduced here): the api test suite has a shared-cache race — `provider_accounts` reads can hit "database table is locked" while parallel account-run threads commit (seen once in a full-suite run, never in isolations or reruns; same class as the RFC-012 §7 contention note). Candidate for a follow-up hardening pass.

## 13. Alternatives rejected (with reasons, for the record)

- **Direct Core writes** (provider-owned connection) — rejected: bypasses scope seam, re-treads the concurrency path behind the busy_timeout incidents. (AUQ-settled.)
- **SystemEvent removal facts** — rejected: entity stamps + delta stream suffice; double bookkeeping. (AUQ-settled.)
- **Master-event storage + local RRULE expansion** — rejected: events are individual instantiations; Google's `singleEvents` expansion is sync-compatible, so masters add a derivation layer for no fidelity gain.
- **`all_day` bool / auto-`occurred_at` on date passage / `occurred_at = starts_at` on backfill** — rejected per D3/D5: null must mean "not happened"; beliefs are never fabricated at ingest.
- **Separate OAuth client** — rejected: RFC-012 designed the shared `google:{subject}` identity for exactly this; one identity, one revocation surface (documented footgun).
- **`metadata.amends` fact-revision convention** — dissolved by the entity model: edits are entity updates + deltas, not new facts.

## 14. Decision log — all settled (AUQ 2026-11-16)

Blockers: contract extension for Core writes; operators + agents may mark occurred (Action-attributed); full-history backfill (`captured_after_fact` marker); removals = entity stamps only.

Minor decisions:
1. Fact kind named **`EventOccurrence`** (entity stays `Event`).
2. Calendar default: **owner-role only**; `calendar_ids` allowlist opts extras in.
3. Cadence: **hourly** (`interval: 3600`) — freshness chosen over KDF thrift; 24 Argon2id unlocks/day accepted. (Mitigation if it ever matters: the offered-but-unapplied systemd memory caps.)
4. Scope posture: **triage default** (no policy at registration; fail-closed catch-all). Per-calendar rules via `metadata.calendar_id` available later.

Defaults adopted without objection: provider registry name `gcal`; participants cap 50 + `metadata.attendee_count`; `self_response` kept in metadata; `status` keeps `tentative`; audit pass = full listing (no `timeMin`) monthly + after reseed; mark-occurred body defaults `occurred_at` to now.

Note: with triage default + full-history backfill, the review backlog carries *two* gates — scope triage and occurrence marking. Both are batchable; both argue for the future review UI / agent reconciliation loop (§7).
