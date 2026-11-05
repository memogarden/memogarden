# Gmail Incremental Sync — Design Review (historyId)

**Status:** Implemented 2026-11-05 (feature/historyid-incremental-sync, live:
seeded at historyId 12515023; hourly timer runs next → incremental →
attachments; switchover to incremental is automatic when the backfill
window is fully ingested)
**Date:** 2026-11-03
**Context:** The progressive oldest-N backfill (batch 500/hour) marches from
the old end of the mailbox; new mail arrives at the *newest* end, where the
oldest-N strategy cannot reach until the entire backfill completes (~11k
messages ≈ 23 hours at 500/hour, or sooner if the machine logs out).
historyId-based incremental sync is the mechanism that closes that gap: it
delivers new mail within the hour at a cost of ~1 API call per run.

**Ingestion policy (user-stated, applies to every pass):** we ingest all
mail except mail currently in TRASH or SPAM. The backfill already satisfies
this (Gmail's default list scope excludes both); rescue events (spam →
inbox) are handled as described below so rescued mail arrives hourly rather
than waiting for the backfill.

## How Gmail historyId works

1. Every Gmail account has a monotonically increasing `historyId`. It appears
   in `users.getProfile` and on every message/list response.
2. `users.history.list(startHistoryId=N)` returns the ordered change records
   since N: `messagesAdded`, `messagesDeleted`, `labelsAdded`, `labelsDeleted`.
3. Expiry: Gmail retains only recent history (roughly 30 days, or less under
   storage pressure). If `startHistoryId` is too old, the call returns **404** —
   the signal to fall back to a full sync.

## Proposed design

**State** — `~/.local/share/memogarden/gmail-sync-state.json`:
`{"account": "…", "history_id": "…"}`. Written after every successful
incremental pass. A dedicated state file (like the search cache) keeps this
derived, resumable-by-deletion state out of Soil; deleting it simply forces
a re-sync. (If per-fact provenance is ever needed, the historyId can also be
recorded as a SystemEvent fact — not required for correctness.)

**Per-hourly-run flow** (new `incremental` pass, inserted into the timer job
between `next` and `attachments`):

1. Load state. If no stored historyId → **seed**: `users.getProfile` → store
   its historyId → done (1 API call). Seeding before any backfill means new
   mail starts flowing immediately while the backfill marches the old end.
2. If stored historyId exists → `history.list(startHistoryId=stored)`:
   - Collect candidate message ids from `messagesAdded` records **and from
     `labelsRemoved` records** — the latter covers rescue events (a message
     moved out of SPAM/TRASH into INBOX), which must be ingested under the
     all-mail-except-trash-and-spam policy. Pure `labelsAdded` records
     (archive/read toggles) are noise and ignored.
   - Deduplicate candidates and filter through the done set
     (gmail_message_ids already in Soil) — the same set-based selection as
     the backfill, so dedup is unified.
   - Fetch each candidate with `get_message` and filter on its **current**
     labels: skip if TRASH or SPAM is present at fetch time (the record may
     be stale — a message spammed and rescued within the same window is
     ingestible). This makes the fetch-time label check the single source
     of truth for the policy, regardless of which history record surfaced
     the candidate.
   - Import the remainder (dedicated batch cap — new mail per hour is
     typically single-digit; the 500 batch cap is for backfill throughput,
     not this pass).
3. On success → store the new historyId (from the history response or
   profile). On **404** (history expired) → log, run the progressive
   oldest-N pass as the resync mechanism, and store a fresh historyId from
   profile. **This is the key simplification:** we already built the full
   sync — the fallback is one function call.

**Failure semantics** — identical to the existing passes: per-message errors
counted and skipped; a killed run loses only its own work; everything is
idempotent (done-set filtering + rfc_message_id dedup). A run with no new
mail costs exactly 1 API call.

## Gotchas accounted for

| Gotcha | Handling |
|--------|----------|
| History expiry (404) | Fallback = progressive oldest-N pass (already built) |
| Label-only churn | Ignore `labelsAdded`/`labelsDeleted` records |
| TRASH/SPAM additions | Filter by labelIds after fetch |
| Multiple devices / parallel runs | Same as today: user timer, single instance |
| historyId recorded before import commit | Store state only AFTER successful import; worst case on crash is a re-delivery, deduped |

## What it does not solve

- The 20k enumeration horizon for the **backfill** (progressive mode's page
  budget) — unrelated to historyId; the backfill marches toward it at 500/h
  (~20k in ~40 logged-in hours). If that horizon approaches, options are:
  raise the page budget (linear API cost), or invert the direction (newest-N
  downward from now, since historyId covers the new end anyway).
- Deletes/edits performed in Gmail are not mirrored retroactively (Soil is
  immutable; a trashed old mail stays in Soil). Consistent with the
  append-only philosophy.

## Effort estimate

One session: `history` client method (~15 lines), state file I/O (~20),
`import_incremental()` (~40), timer wiring, tests with a scripted history
API (expiry, label noise, trash filtering, seed-from-profile). Design risk:
low — every mechanism it needs (done set, dedup, batch caps, state files)
already exists and is tested.
