# RFC-012: Provider Accounts — Multi-Account Ingestion

**Version:** 1.3
**Status:** Accepted (implemented 2026-11-14)
**Author:** MemoGarden Project
**Created:** 2026-11-14
**Depends on:** RFC-004 v2.2 §12.2 (In-Server Provider Runtime), RFC-005 v8 (API Design / CLI Verb Grammar), RFC-011 v1.2 §4.8 (Providers & Scope Policy), HACM Credential File Schema v3

## Abstract

Every ingestion provider currently serves exactly **one upstream account**, hard-coded
as a value inside the provider's registry config. This RFC introduces the
**Provider Account**: a first-class infrastructure record binding a provider to a
verified upstream identity (platform + subject) and a credential reference, so one
provider can ingest from N accounts with isolated sync state, per-account run
attribution and scheduling, per-account failure isolation, and per-account scope
labeling. Tokens remain in HACM and **never transit the API**. The design is
platform-forward: a future Google Calendar or Docs provider shares the account
identity (and refresh token) with Gmail; a future GitHub provider fits the same
account record with different credential semantics.

Motivating event: the operator's IFTTT forwarding applets — the current workaround
for reading a second mailbox — were paused by IFTTT's credit paywall. Retiring the
forwarding crutch requires MemoGarden to read each mailbox directly.

## 1. Motivation

### 1.1 Problem Statement

1. **The single-account assumption is structural, not incidental.** The gmail
   provider requires `config["account"]` (`gmail_importer/provider.py`), keys all
   sync state into flat single files, and builds its done-set from *all* Email
   facts with no account filter. Nothing about the current shape can ingest two
   mailboxes.
2. **Ingested facts are unattributable.** Email and Attachment metadata record
   `provider: "google"` but never *which mailbox* a fact came from. With more
   than one account, provenance — and therefore per-account scoping under
   RFC-011 — is lost at write time.
3. **A latent wrong-account bug waits for N=2.** The attachment extractor
   refetches every email bearing an attachment through **one** API client built
   from one account's token (`attachments.py`). Account B's mail fetched with
   account A's token returns 404 and is currently *counted as a deletion*.
4. **Accounts have no lifecycle.** A revoked or expired refresh token just fails
   runs forever; there is no `needs_reauth` state, no surface that says "re-link
   this account". Worse, `auth-login` stores a token under whatever `--account`
   string the operator typed, with no check that the token actually belongs to
   that mailbox.
5. **Ingestion currently depends on an external crutch.** IFTTT applets forward
   one mailbox into another to route mail into the single ingested account. This
   is fragile (paywalled, paused without warning) and leaks every forwarded
   message to a third-party SaaS.

### 1.2 Current State

- **Registry.** `providers` (core.db, infrastructure tier, outside the delta
  chain) is keyed by `name`, with one opaque `config` JSON blob, one
  `last_run_at`/`next_due_at` watermark, one `scope_policy`, and one single-flight
  lock per name (`provider_runs` partial unique index). `provider_runs` records
  `(id, provider_name, status, started_at, finished_at, triggered_by, detail)`.
  No account concept exists anywhere in the registry.
- **Runtime.** `run_all_due` → `claim_run` (409 on concurrent) → daemon thread →
  `cls(config=config)` → `sync(since=...)` generator → per-fact scope policy →
  `create_fact` + `commit`. The `since` watermark is per provider name; note the
  gmail provider *ignores* it (its true cursor is the `historyId` in
  `gmail-sync-state.json`).
- **OAuth/token layer.** `gmail-oauth` stores client credentials in HACM entry
  `google:oauth-client` and per-account refresh tokens in entries
  `google:{account_id}` (`type=oauth_refresh`). HACM's store is a flat encrypted
  dict keyed by credential id — **multiple `google:<email>` entries are already
  natural**. Each sync run mints a fresh access token via the refresh grant;
  the stored refresh token is never rotated. Scopes requested: `gmail.readonly`.
  Consent is a CLI-local loopback flow on `localhost:8080`.
- **State files** (all under `~/.local/share/memogarden/`, all flat, none
  account-keyed): `gmail-sync-state.json` (`{account, history_id, updated_at}`,
  whole-doc overwrite with an account-mismatch guard that just reseeds),
  `gmail-excluded-ids.json`, `gmail-search-cache.json` (CLI progressive mode
  only). The done-set is derived per run by scanning all Email facts for
  `$.gmail_message_id`.
- **HACM.** `CredentialStore` construction pays a machine-bound Argon2id KDF
  (~1 GiB, derived from `/etc/machine-id` + passphrase); lookups after
  construction are cheap. Stores cannot move between machines — re-consent per
  account is required on host migration (accepted posture, RFC-001 §6).

### 1.3 Design Goals

- **G1 — Account as durable identity.** An account is a platform-level identity
  (Google account, GitHub user), not a provider instance. Capability providers
  attach to it; credentials are per-identity.
- **G2 — Token custody invariant.** Tokens live only in HACM and never transit
  the API, the registry, logs, or run records.
- **G3 — Per-account isolation.** Sync state, cursors, done-sets, run records,
  scheduling, and failure blast radius are all per account.
- **G4 — Attribution at creation.** Every fact written by an account-backed
  provider carries `metadata.account`.
- **G5 — Operator workflow via CLI.** Verb-first grammar (RFC-005): register,
  list, show, verify, remove, run — with `account` as the noun.
- **G6 — Contained runtime change.** Account-less providers (e.g. tada, mbox)
  keep their current behavior untouched.
- **G7 — Platform-forward.** Adding Google Calendar/Docs (same identity,
  widened scopes) or GitHub (different credential shape) requires no new
  schema or custody machinery.

## 2. Core Principle

**An account is an identity, not a provider instance.** Providers execute
*capabilities* against *accounts*. The gmail provider is the first capability on
Google accounts; calendar and document providers will be further capabilities on
the same identities. The registry records the binding (provider ↔ account ↔
credential reference); the corpus records attribution (which identity produced
each fact); HACM records the secret. Each concern has exactly one home.

## 3. Vocabulary

- **Provider** — a registered capability module (`providers.name`), e.g. `gmail`.
  Not a principal (RFC-011 §4.8).
- **Account** — a binding of one provider to one verified upstream identity.
  **Not a principal**: accounts hold no grants, enter no scopes, and gate
  nothing (they are one level further from authority than providers, which are
  themselves non-principals). Accounts are the *subject* of attribution and
  labeling, never the *holder* of access.
- **Platform** — the identity ecosystem: `google`, `github`, …
- **Subject** — the verified upstream identity string (email address, username).
  Verified at link time (§6.3), never operator-asserted alone.
- **Credential reference** — the HACM credential id holding the secret, by
  convention `{platform}:{subject}`. This is a *reference*, never the secret.
  It doubles as the cross-provider join key: the `gmail` and (future) `gcal`
  providers may both hold accounts with subject `me@gmail.com`, both pointing at
  the single HACM entry `google:me@gmail.com`.

## 4. Model

### 4.1 The `provider_accounts` Table

Added to core.db's infrastructure tier (alongside `providers`, `provider_runs`;
outside the entity/delta hash-chain machinery; **no secrets in this table**):

```sql
CREATE TABLE IF NOT EXISTS provider_accounts (
    id TEXT PRIMARY KEY,                     -- uuid
    provider_name TEXT NOT NULL,             -- registry name this account feeds
    platform TEXT NOT NULL,                  -- 'google' | 'github' | ...
    subject TEXT NOT NULL,                   -- verified upstream identity
    label TEXT,                              -- operator nickname (CLI display)
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'needs_reauth', 'revoked', 'disabled')),
    credential_ref TEXT NOT NULL,            -- HACM credential id ({platform}:{subject})
    config TEXT NOT NULL DEFAULT '{}',       -- JSON; per-account provider config
    capabilities TEXT NOT NULL DEFAULT '[]', -- JSON; capability grants on this identity
    error TEXT,                              -- last failure summary (for CLI display)
    interval INTEGER,                        -- NULL = inherit provider interval
    next_due_at TEXT,                        -- per-account due time (NULL = due now)
    last_run_id TEXT,
    last_run_at TEXT,
    last_status TEXT,
    last_verified_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (provider_name, subject)
);
CREATE INDEX IF NOT EXISTS idx_provider_accounts_due
    ON provider_accounts(provider_name, status, next_due_at);
```

Precedence rule for config: `{**provider.config, **account.config}` — provider
config holds shared concerns (`hacm_path`, `passphrase_file`, defaults);
account config holds per-identity overrides (`batch_limit`, state paths). Paths
not overridden derive per subject (§4.6).

### 4.2 Account Lifecycle

| Transition | Trigger |
|---|---|
| → `active` | successful registration (verified consent) or successful `verify` after `needs_reauth` |
| → `needs_reauth` | run refresh grant fails (`invalid_grant`, 401); run outcome records `error` |
| → `disabled` | operator pause (`PATCH`); heartbeat skips, state preserved |
| → `revoked` / deleted | operator `remove` (optionally revoking the token upstream first) |

Status is written by exactly three code paths: run outcomes (failures only),
the verify endpoint, and operator CLI/API actions. Runs never silently retry a
`needs_reauth` account — the heartbeat skips it until an operator re-consents.

### 4.3 Token Custody

- **Storage.** One HACM entry per account: `{platform}:{subject}`
  (`type=oauth_refresh`, material = refresh token, metadata = scopes +
  token endpoint), plus one shared client entry per platform:
  `google:oauth-client`. Existing entries already conform; **no migration of
  secrets is required**.
- **Unlock amortization.** Each run thread constructs the `CredentialStore`
  **once** and resolves all its account lookups from that single unlocked
  instance. N accounts must never mean N Argon2id KDFs per heartbeat.
- **Link-time verification (new).** After code exchange, the registration flow
  calls the platform's profile endpoint (`users/me/profile` → `emailAddress`)
  and compares it to the intended subject. On mismatch: abort, store nothing.
  This closes the "typo'd `--account`" hole before multi-account makes it
  dangerous.
- **Refresh semantics unchanged.** Refresh grant per run; stored refresh token
  is never rotated (Google does not rotate in this grant type).

### 4.4 Run Attribution

`provider_runs` gains one column:

```sql
ALTER TABLE provider_accounts;  -- (new table above)
ALTER TABLE provider_runs ADD COLUMN account_id TEXT NOT NULL DEFAULT '';
```

The empty string is the explicit sentinel for *provider-level* (account-less)
runs — used by all historical rows and all account-less providers. A new partial
unique index replaces the old single-flight lock, making the claim
**per (provider, account)**:

```sql
DROP INDEX IF EXISTS idx_provider_runs_single_flight;
CREATE UNIQUE INDEX IF NOT EXISTS idx_provider_runs_single_flight
    ON provider_runs(provider_name, account_id) WHERE status = 'running';
```

Consequences: one wedged account cannot block another account's run; a manual
`run` of account B while the heartbeat works account A returns 409 only for the
colliding account.

### 4.5 Scheduling & the Runtime

Due-ness moves to the account for account-backed providers:

- `get_due_providers` excludes providers that have ≥1 row in
  `provider_accounts` (subquery `NOT EXISTS`); a companion
  `get_due_accounts` returns `(provider, account)` pairs where the provider is
  enabled, the account is `active`, and `next_due_at` is due (account
  `interval` NULL = inherit provider interval).
- `run_all_due` expands to per-account work items. Each item: `claim_run(name,
  account_id)` → thread → provider constructed with an `account` context dict
  `{id, platform, subject, label, credential_ref, config}` (never secrets) →
  `sync(since=last_success_for(name, account_id))` → `finish_run` updates the
  **account row's** `last_run_*`/`next_due_at` and the provider row's aggregate
  `last_run_*` columns.
- **Provider construction.** Runtime inspects the class `__init__` signature
  (same detection style as `create_relations`): classes accepting an `account`
  parameter are account-backed and receive the context; others receive nothing
  and behave exactly as today (G6).
- `cleanup_orphaned_runs` extends unchanged across the account dimension
  (stale `running` rows marked failed on startup).
- The `since` watermark becomes per (provider, account) from `provider_runs`
  history. (Gmail ignores it in favor of `historyId`; the watermark remains
  correct for providers that use it.)

### 4.6 Per-Account State Isolation

State files gain a subject-qualified form, derived by default from config:

- `gmail-sync-state.json` → `gmail-sync-state--{subject}.json`
- `gmail-excluded-ids.json` → `gmail-excluded-ids--{subject}.json`
- `gmail-search-cache.json` → `gmail-search-cache--{subject}.json`

The whole-doc-overwrite pattern per file is retained (atomic rename, trivial);
per-account *files* beat per-account *sub-docs* precisely because the existing
store/load code needs no structural change. The account-mismatch guard stays as
a belt-and-braces check. Explicit absolute paths in the live provider row's
config continue to win (the legacy account keeps its existing filenames via
migration config, §8).

Done-sets gain the account filter:
`json_extract(metadata, '$.gmail_message_id')` scans become
`... AND json_extract(metadata, '$.account') = :subject`. Gmail message ids are
per-mailbox namespaces; the merged global set was only correct while one mailbox
existed.

The attachment extractor routes every refetch through a client built from the
**owning fact's** `metadata.account` (per-account client, lazily constructed
per account per run). This simultaneously fixes the latent 404-masquerading-as-
deletion bug (§1.1.3).

### 4.7 Fact Attribution & Dedup

- **Attribution.** `translate_gmail_to_email` and the attachment translator
  stamp `metadata.account = subject` on every fact, next to
  `metadata.provider`. Metadata sits outside `Fact.compute_hash` (which covers
  `data` only), so stamping and backfilling never disturb integrity hashes.
- **Dedup — per-account facts (decided D1, §11).** The rfc index key becomes
  `(account, rfc_message_id)`: each mailbox yields its own fact for the same
  message; copies remain linkable via the shared content hash of `data`.
  Rationale: per-fact scope grants cannot express "agent X may see this only
  via the work account" if one fact carries two mailboxes. Identical attachment
  binaries still store once (the `AttachmentStore` is content-addressed by
  sha256) — duplication costs rows, not bytes.
- **Threading stays global.** `replies_to` lookup by `data.rfc_message_id`
  continues to match across accounts: a reply ingested from account B may
  thread to the original in account A. This is a feature (conversation view
  transcends mailbox boundaries).
- `schemas/email.yaml` gains `metadata.account` (documented, optional for
  legacy mbox facts); the JSON Schema already permits additional metadata.

### 4.8 Scope Policy Integration

No grants-layer change. The runtime's `_apply_scope_policy` merges
`{**fact.data, **metadata}` before rule evaluation, so the attribution field is
immediately matchable:

```json
{"rules": [
  {"match": {"account": "work@company.com"}, "scope": "<work-scope-uuid>"},
  {"match": {"account": "ngjunsiang@gmail.com"}, "scope": "personal"}
], "default": "triage"}
```

Per-account labeling therefore falls out of RFC-011 §4.8 with zero new
enforcement machinery, and the fail-closed `triage` default continues to govern
any unconfigured provider.

### 4.9 The Attachment Pass

The heartbeat unit's second `ExecStart` (CLI attachments pass with
`--account $INGEST_ACCOUNT`) must become account-aware: iterate the provider's
active accounts (or accept `--account` per invocation, once per account).
Whether this pass eventually merges into the provider contract is an existing
open question (tracked alongside the heartbeat unit rename); this RFC only
requires it not break under N accounts.

### 4.10 Ingestion Order Policy (v1.2, 2026-11-14)

**Official policy: history is ingested reverse-chronologically.** A newly
linked account begins at its most recent email and walks backward in time
until its oldest email is ingested — the user gets useful, current material
from the first heartbeat, and the rest of the history accrues batch by
batch in the background.

- Every sync runs two stages: the historyId incremental walk (incoming
  mail, bounded latency) followed by the backward-fill batch (the most
  recent un-ingested mail first) when the account's backfill is not yet
  complete and the incremental stage did not truncate.
- The backward walk is a plain paged walk from the head of
  `messages.list` (natively newest-first — no date-window search, no
  search cache; the CLI's oldest-first recovery path owns that file and
  the two orderings must never share it). Depth grows as the backfill
  progresses but stays bounded by a per-run page budget (40 pages ≈ 20k
  ids); hitting the budget DEFERS the remainder to the next run rather
  than failing it, so a mailbox beyond the horizon never wedges the
  account — it backfills as far as the horizon allows and the incremental
  stage keeps working regardless.
- Completion is explicit and conservative: the per-account sync state
  records `backfill: complete` only when a walk covered the whole listing
  AND every id in the draining batch resolved (fetched or policy-skipped —
  transient fetch failures keep it running for retry). Completed accounts
  stop enumerating. Accounts predating this policy self-settle the same
  way: their first post-deploy sync enumerates once, finds everything
  already ingested, and marks complete.
- Threading under reverse-chronological order: a reply is stored before
  its parent, so the forward `replies_to` resolution misses.
  `create_relations` therefore also reverse-links — when a fact lands,
  stored mails whose `in_reply_to`/`references` point at it are linked to
  it — making threading eventual-consistent under any ingest order.
- A truncated incremental batch defers the backward-fill stage to the next
  heartbeat: incoming mail always outranks history. New mail is never
  double-yielded within a run (the backward walk skips ids the
  incremental stage already served).
- **Horizon walk (v1.3, 2026-11-16):** mailboxes deeper than one run's
  page budget (nyjc: >20k) continue via a tightening `before:` date
  cursor. When a walk's page budget covers only already-ingested ids,
  the boundary message's date (+1 day) is persisted as `backfill_before`
  in the sync state and the next walk enumerates `before:<cursor>` —
  the not-yet-covered older region — repeating until the bounded listing
  drains (then `complete`). Never fails; the 20k cap becomes a pacing
  detail, not a ceiling.
- The manual `ingest next` recovery path (oldest-first) is unchanged but
  superseded for this purpose — the policy needs no operator action.

## 5. Behavioral Invariants

- **INV-1 — No secret anywhere but HACM.** No token material in core.db,
  `provider_accounts`, `config`, `provider_runs.detail`, logs, or API payloads.
- **INV-2 — Consent is operator-local.** The API never sees OAuth codes or
  tokens. Registration runs consent in the CLI against the local HACM; the API
  records only the credential *reference*.
- **INV-3 — Subjects are verified.** No account row exists whose credential was
  not profile-verified against its subject at link time.
- **INV-4 — Failure isolation.** One account's expired token fails only that
  account's run and never blocks another account's run or the heartbeat.
- **INV-5 — One unlock per execution.** At most one Argon2id KDF per run
  thread per heartbeat, regardless of account count.
- **INV-6 — Accounts are not principals.** No grants, no scope entry, no access
  authority; they label nothing themselves — RFC-011 policy labels through them.
- **INV-7 — Attribution completeness.** Every fact written by an account-backed
  provider carries `metadata.account`.
- **INV-8 — Cursor monotonicity, per account.** `historyId` advances only after
  full generator consumption, per account — existing semantics, now scoped.

## 6. Management Protocol

### 6.1 API Surface

All under `/api/v1/providers/<name>/accounts`, authenticated like provider CRUD
(`X-API-Key`; accounts are not principals, so no JWT gate — RFC-010's
operator-gated issuance applies to *agent identity*, not ingestion identity):

| Verb | Path | Semantics |
|---|---|---|
| POST | `/accounts` | create account row (reference only; never tokens); rejects non-account-backed providers and platform mismatches |
| GET | `/accounts` | list (status, subject, label, last run) |
| GET | `/accounts/<id>` | detail |
| PATCH | `/accounts/<id>` | label, config, status (`disabled`/`active`/…), interval |
| DELETE | `/accounts/<id>` | delete the registry row only (v1.1: the HACM credential is operator-managed and deliberately untouched — custody stays host-side, INV-2; upstream revocation at Google remains a manual/host-side action) |
| POST | `/accounts/<id>/verify` | server-side probe via the provider's `verify_account` hook (refresh grant + profile + subject cross-check); flips `needs_reauth` → `active` on success |
| POST | `/accounts/<id>/run` | manual run, 202/409 per single-flight |

Platform validation at POST (v1.1 as built): account registration requires a
provider whose class is account-backed (`__init__` accepts an `account`
context — a provider that would silently ignore accounts is rejected) and,
when the class declares `PLATFORMS`, a matching platform (mismatch → 400).

### 6.2 CLI Surface (RFC-005 verb-first)

```
memogarden register account --provider gmail --account you@gmail.com [--label work] [--config k=v]...
memogarden list    accounts --provider gmail
memogarden show    account --provider gmail (--id | --label | --subject)
memogarden verify  account --provider gmail (--id | --label | --subject)
memogarden remove  account --provider gmail (--id | --label | --subject)
memogarden run     account --provider gmail (--id | --label | --subject)
```

`register` is the shared RFC-005 verb; `account` is a new noun. Client side
follows the `register provider` template exactly: argparse noun parser,
payload builder, dispatch branch, `AccountAPI` httpx wrapper, mirrored pydantic
models.

**v1.1 division of labor (as built):** the `memogarden` CLI is the generic
registry surface — it records/repairs account rows and never sees tokens.
The OAuth consent itself is provider knowledge and lives in the provider's
own CLI; for gmail the operator command is:

```
python -m gmail_importer.ingest auth-login --account you@gmail.com \
    [--label work] --register [--api-url URL] [--api-key KEY]
```

which performs the whole flow of §6.3 in one command (consent → verify →
HACM write → account-row POST; 409 on an existing row = re-link, the fresh
token refreshes the HACM entry in place). `memogarden register account`
remains available for registering a row whose credential already exists in
HACM, and for future platforms whose linking is not OAuth-shaped.

### 6.3 Registration Flow (CLI-local, sequential)

1. Resolve provider from the registry; check it is account-backed and the
   platform matches (`PLATFORMS`).
2. Print authorization URL (`access_type=offline`, `prompt=consent`, random
   `state`); serve loopback callback on `localhost:8080` (existing
   `SimpleCallbackServer`; sequential registrations only — document the port).
3. Exchange code → tokens (refresh token required).
4. **Verify subject**: profile call; compare to `--account`; mismatch aborts
   with nothing stored (INV-3).
5. Write HACM entry `{platform}:{subject}` (`type=oauth_refresh`) — under the
   *verified* subject, normalized to lowercase.
6. POST the account row (credential_ref only — INV-2).
7. First sync **seeds** the historyId AND begins the automatic history
   backfill (§4.10): from the first heartbeat the account ingests its most
   recent mail first and walks backward toward its oldest email, one
   `batch_limit` per run, no operator action.

## 7. Failure Modes & Edge Cases

- **`invalid_grant` mid-heartbeat** → that run fails, account → `needs_reauth`
  with `error` recorded; subsequent heartbeats skip it. Re-link = `verify`
  after CLI re-consent, or re-`register` (same subject updates in place).
- **Consent screen for a new Google account** shows the unverified-app warning;
  the OAuth app is In Production with a personal-use policy — fine at personal
  scale; each account consents once.
- **Port 8080 busy** during registration → sequential registration is the
  contract; error message says so.
- **OAuth client disabled at Google** → every account fails refresh; each
  account records `needs_reauth` independently; operator fixes the client,
  `verify` re-activates in bulk.
- **IFTTT overlap during transition** → per-account dedup means a forwarded
  copy in the primary mailbox and the original in the source mailbox are
  **distinct facts by design** (D1). Migration order therefore matters:
  cancel IFTTT applets *before* the second account's first sync (§8).
- **Legacy facts without `metadata.account`** → backfilled during migration
  (§8); the attachment pass treats a missing account as the sole legacy
  account during the backfill window.
- **Same subject under two providers** (gmail + future gcal) → distinct account
  rows (`UNIQUE` is per provider), shared HACM entry via the
  `{platform}:{subject}` convention. Revoking one row's `--revoke` kills the
  shared token for both — the CLI must warn when `credential_ref` is referenced
  by another account row.
- **Orphaned per-account runs** after server restart → existing
  `cleanup_orphaned_runs` semantics, now account-scoped.

## 8. Migration & Rollout

**M1 — system**: `provider_accounts` DDL + `provider_runs.account_id` + index
replacement (next migration in the chain after `20261113`; `core.sql` fresh-init
path updated in step).

**M2 — api**: registry CRUD (`provider_accounts`), endpoints (§6.1), runtime
per-account scheduling/claims/finish (§4.5), per-account `since`. Account-less
providers verified unchanged (regression tests).

**M3 — gmail-importer + gmail-oauth**: account context parameter; per-subject
state paths; done-set filters; `metadata.account` stamping; attachment
extraction routing by owning account; link-time verification in the OAuth
store path.

**M4 — client**: CLI verbs + `AccountAPI` (§6.2).

**M5 — live migration (order matters)**:
1. Cancel the IFTTT applets (before any second-account ingestion; §7).
2. Back up `core.db` + `soil.db`; stop the server.
3. Backfill `metadata.account` on existing Email/Attachment facts while the
   server is stopped (bulk `UPDATE`; legal — metadata is outside the
   integrity hash). The done-sets additionally carry a pre-backfill defense
   in code: a corpus with NO attributed gmail fact anywhere falls back to
   the unfiltered legacy scan, so an order slip re-runs dedup rather than
   re-ingesting duplicates.
   ```sql
   UPDATE item SET metadata = json_set(metadata, '$.account', 'ngjunsiang@gmail.com')
   WHERE _type IN ('Email', 'Attachment')
     AND json_extract(metadata, '$.provider') = 'google'
     AND json_extract(metadata, '$.account') IS NULL;
   ```
4. Start the server (the 20261114 migration applies on boot).
5. Insert the account row for the existing identity
   (`ngjunsiang@gmail.com`, `credential_ref = google:ngjunsiang@gmail.com`,
   status `active`, **config carrying the current absolute state paths** so
   it keeps its historical filenames).
6. **Strip the per-account state paths from the `gmail` provider row's
   config** (PATCH `/api/v1/providers/gmail`) — provider config is merged
   into EVERY account; leaving `sync_state_path`/`excluded_ids_path` there
   would make all accounts share one cursor file. After the strip, new
   accounts derive subject-qualified paths (§4.6).
7. Update the heartbeat unit: the attachments `ExecStart` drops
   `--account $INGEST_ACCOUNT` so the pass covers every active registered
   account (`scripts/install-user-service.sh` updated too for fresh
   installs); `systemctl --user daemon-reload`.
8. Verify: trigger a legacy-account run (202 → provider_runs row with
   `account_id` set, account `last_status=success`), confirm the heartbeat's
   attachments pass reports the registered-account scope.
9. `auth-login --register` the second mailbox; first sync ingests it fully.

**Rollback.** All changes live in the infrastructure tier (outside the delta
chain): dropping the table/column and reverting the code restores pre-RFC
behavior; HACM entries for new accounts can be deleted directly.

## 9. Security Analysis

- **Authority.** Account CRUD rides provider-CRUD authority (`X-API-Key`), the
  same power that already registers arbitrary ingestion code — no new
  privilege frontier. Accounts grant no access (INV-6); exposure of ingested
  material continues to be governed exclusively by RFC-011 labels + grants,
  fail-closed to `triage`.
- **Secrets posture.** INV-1/2 keep the API, registry, and logs token-free.
  The machine-bound HACM store (Argon2id over `/etc/machine-id`) means account
  credentials do not survive host migration — re-consent per account on an RPi
  move is accepted toil (RFC-001 §6 posture), now multiplied by account count
  and acknowledged here.
- **KDF discipline.** INV-5 prevents N accounts from becoming N ~1 GiB KDF
  transients per heartbeat.
- **Registration spoofing.** INV-3 prevents storing a token under a subject it
  doesn't belong to; combined with per-account state keying, a mislabeled
  account cannot cross-contaminate another mailbox's cursor or done-set.
- **Shared-token revocation.** Documented footgun (§7): `--revoke` on a
  `credential_ref` shared by future capability providers kills all of them;
  CLI warning required.

## 10. Non-Goals & Future Work

- **Google capability widening** (Calendar/Docs/Sheets on an existing Google
  identity via incremental consent, `include_granted_scopes`) — the model
  reserves it (`capabilities` column, shared `credential_ref`); implementation
  is a future RFC paired with the first non-mail Google provider.
- **GitHub provider shape** (PAT vs GitHub App semantics; PATs have no refresh
  grant — `verify` degrades to a validity probe per platform).
- **Studio/UI account management** — API-only for now, mirroring RFC-010's
  posture.
- **Per-account scope policies beyond match rules** (e.g. per-account default
  scope) — the per-provider policy plus `account` matching is sufficient.
- **Credential rotation automation** and **headless re-consent tooling**.

## 11. Decision Log (Executed)

| # | Decision (2026-11-14) | Choice | Rationale |
|---|---|---|---|
| D1 | Cross-account dedup | **Per-account facts**; dedup key `(account, rfc_message_id)` | Per-fact scope grants can't discriminate accounts on a shared fact; binaries already content-addressed, so duplication costs rows not bytes |
| D2 | Account entity shape | **`provider_accounts` table**; one provider row iterates its accounts | Account = durable identity shared by future capability providers; CLI noun; no per-account duplication of interval/policy/config |
| D3 | Run attribution | **`provider_runs.account_id` (NOT NULL DEFAULT '')**, one row per provider-account; single-flight becomes per (provider, account) | Per-account status/watermark queryable in SQL; feeds `needs_reauth` directly; isolates wedged accounts |

Also adopted: the **tokens-never-transit-the-API** invariant (INV-2) and
**retirement of the IFTTT forwarding applets** upon rollout (§8 M5).

## Open Questions

1. Should the standalone attachment CLI pass merge into the provider contract
   (retiring the heartbeat unit's second `ExecStart`)? Tracked alongside the
   `memogarden-heartbeat` rename (provider-runtime issue #1); this RFC only
   requires account-awareness if it stays.
2. Legacy OOB paste-code flows in `gmail-oauth` (`cli.py`, `oauth-exchange.py`)
   predate the loopback server — delete during M3 or leave dormant?

## References

- RFC-004 v2.2 §12.2 — in-server provider runtime, origin pin, registry
- RFC-005 v8 — API design; verb-first CLI grammar (`register provider`)
- RFC-010 v1.1 — agent identity (operator-gated issuance; accounts distinguished from principals)
- RFC-011 v1.2 §4.8 — providers label at creation; scope policy rules
- HACM Credential File Schema v3; `hacm/hacm/crypto.py` (machine-bound Argon2id KDF)
- `providers/gmail-importer/gmail_importer/` — `provider.py`, `importer.py`, `attachments.py`, `gmail_api.py`
- `gmail-oauth/__init__.py` — `GmailOAuthClient`, loopback flow, token storage
- `memogarden-api/api/provider_registry.py`, `api/provider_runtime.py` — registry CRUD, claims, runtime
- `memogarden-system/system/schemas/sql/core.sql` — infrastructure-tier DDL
- `memogarden-client/mg_client/cli.py` — argparse verb-first CLI pattern

## Revision History

| Version | Date | Notes |
|---|---|---|
| 1.0 | 2026-11-14 | Initial draft. D1–D3 decided via operator review; rollout plan M1–M5. |
| 1.1 | 2026-11-14 | As-built amendments: §6.2/§6.3 — OAuth consent lives in the provider's own CLI (`gmail_importer.ingest auth-login --register`, one command per account); the `memogarden` CLI stays the token-free generic registry surface. §6.1 — DELETE removes the registry row only (HACM custody stays host-side; upstream revocation manual); account registration additionally rejects non-account-backed providers. §8 M5 — full deployment runbook (backfill SQL, provider-config strip, heartbeat-unit update). Status → Accepted. |
| 1.3 | 2026-11-16 | §4.10 horizon walk: mailboxes deeper than one run's page budget (nyjc: >20k) continue via a tightening `before:` date cursor persisted in sync state (`backfill_before`); boundary date captured from the budget-edge message. Observed live: nyjc soft-stalled at exactly 20,000 (Apr 2014→) under v1.2; v1.3 removes the ceiling. |
| 1.2 | 2026-11-14 | §4.10 Ingestion Order Policy (operator decision): new accounts ingest history reverse-chronologically — most recent first, one batch per heartbeat, until the oldest email is ingested. Provider implements it: two-stage sync, `backfill` sync-state field, conservative completion (transient fetch failures keep it running), paged newest-first walk bounded per-run (defer, never fail, beyond the 20k horizon), reverse-linked `replies_to` threading, run-local double-yield guard. §6.3 step 7 updated. |
