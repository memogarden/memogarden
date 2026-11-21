# RFC-015: Operator Console & Operator CLI — v1

**Status**: APPROVED (session 2026-11-18/19; shape discussed and confirmed with the operator before implementation — no dashboard, DELE badge naming, CLI verb tree accepted). This RFC is the design of record for the operator surface: the operator-only/delegable action taxonomy, the server additions, the Operator Console UI, and the operator CLI verb tree.
**Related**: RFC-001 §4 (auth posture), RFC-004 §12.2 (provider runtime), RFC-005 (Semantic API + verb grammar), RFC-010 (agent identity — "identity is issued, never assumed"), RFC-011 (scopes/grants — SG-1…SG-11, D1/D2), RFC-012 (provider accounts), RFC-013 E3 (restricted key class — decided, not built here), RFC-014 §6 (mark-occurred).

---

## 1. Problem

Every operator-governed surface is JWT-only by design — identity issuance (RFC-010 INV-1), grants (RFC-011 SG-4), scope minting (D1 `scope_mint_denied`), provider scope-policy ("boundary configuration is operator-governed") — while the only human transports are raw `curl` with a hand-minted JWT and three pre-RFC-010 Flask pages (login, api-keys, agents). The CLI is structurally API-key-only (`AuthManager` hard-codes `api_key`; keys as Bearer are rejected by design), so it cannot carry the operator role at all. Grants have no UI anywhere; providers, accounts, run history, occurrence review, and the Action audit have no human surface; the admin password cannot be changed (no endpoint exists); run history has no read endpoint (`provider_runs` is internal).

## 2. Operator surface of record (taxonomy)

Governing frame: **the operator defines authority and trust; agents exercise it.** Three principal classes: operator JWT (sovereign, SG-11), bound agent key (`core_…` principal), unbound key (username frame, `actor_type=api_key`).

### 2.1 Operator-only (no delegation)

| # | Action | Invariant | Enforcement today |
|---|---|---|---|
| 1 | Mint agents; mint/bind/unbind/revoke keys | RFC-010 INV-1; credentials cannot manage credentials | unconditional (JWT-only) |
| 2 | Issue/revoke scope grants (incl. expiry) | RFC-011 SG-4 | unconditional (JWT-only) |
| 3 | Mint/delete scopes | RFC-011 D1 (`scope_mint_denied`) | unconditional |
| 4 | Provider scope-policy | boundary config is operator-governed | unconditional (JWT-only) |
| 5 | Flip `MEMOGARDEN_SCOPE_ENFORCEMENT` | turns advisory label gates into enforced ones | env/deploy act (not an endpoint) |
| 6 | Register/remove provider modules | executable-code trust (origin-pinned to provider roots; today key-reachable via RFC-012 ride-along — candidate to tighten, NOT tightened here) | advisory |
| 7 | Occurrence adjudication (final say) | RFC-014 §6 decision — review is the operator's; bound agents may mark | binding gate unconditional (unbound 403) |
| 8 | High-stakes triage destinations (e.g. statements → Finances; minting Finances) | belief-correcting, exposure-widening | advisory until #5 |
| 9 | Operator account security (password change) | — | **new in this RFC** (§3.1) |

Architecture-level operator-only items stay outside the API by design: HACM custody, deploy/env, backups.

### 2.2 Agent-delegable management actions the operator carries out or verifies

| Action | Delegation status | Operator verification |
|---|---|---|
| Provider run triggers (run-all / per-provider / per-account) | any principal today; RFC-013 E3 restricted key class decided, **not built here** | run history (§3.2), created/error counts, gcal audit diff |
| Triage sorting (enter → amend-relabel → leave) | delegable with grants on triage + target scopes; D2 gates every label flip symmetrically | relabel audit trail (SG-8 before/after); ambiguous/high-stakes items stay with the operator |
| mark-occurred drafts | bound agents allowed; unbound 403 | occurrence review queue (§4.5) |
| Subscription/feed curation | plain `amend` | feed health + supersession chain |
| Account config PATCH | server exists; **CLI client method new here** | override visibility |
| Credential verify probes | delegable; the OAuth re-consent itself is human-only | `needs_reauth` queue |
| Audit/arbitration queries | any principal | — this is the verification instrument |

## 3. Server changes (memogarden-api)

### 3.1 `POST /auth/change-password` (new, JWT-only)

Body `{current_password, new_password}`. Verifies the current password (`middleware/service.verify_credentials`), stores `service.hash_password(new_password)`. Minimum 8 chars per the register contract. Explicit `core._conn.commit()` (auth_bp convention). Authenticated via `_authenticate_jwt()` inside the handler. Closes the long-standing rotation-hygiene gap (admin password snapshotted 2026-11-05 pending exactly this).

### 3.2 Run-history reads (new, JWT-or-API-key)

- `GET /api/v1/providers/<name>/runs?account_id=&status=&limit=` — recent runs for a provider (`provider_runs` is already indexed `(provider_name, started_at DESC)`); default limit 50, cap 200.
- `GET /api/v1/runs/<run_id>` — single run by id.
Read-only, non-governed operational visibility: matches provider GET's principal class today. Registry functions `list_runs` / `get_run` join the existing `claim_run`/`finish_run` family.

### 3.3 Operator-only console reads (new, JWT-only via the `set_scope_policy` in-handler pattern)

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/triage?limit=&offset=&source=` | facts currently labeled into the Triage scope (uuid resolved from the builtin `Triage / Untrusted` entity), newest first, + total count |
| `GET /api/v1/scopes/overview` | scopes (entity table) + per-scope labeled-item counts (facts `metadata.scopes` via `json_each` + entities `data.scopes`) + grants (api_keys.grants) + provider scope policies |
| `GET /api/v1/events/pending-review?window_days=&limit=` | Events with a past `ends_at` (or past date-only start), `occurred_at` null, no occurrence fact — the review queue (probes the universal Event fact since the 2026-11-19 ontology convergence, #31) |

All three are reads for the sovereign operator; enforcement-flag state does not change their results (operator sees everything either way).

### 3.4 Non-goals (explicit)

- SSE `?token=` query-param auth (studio observe mode stays broken; backlog — needs a short-lived ticket design).
- RFC-013 E3 capability flags on api_keys (decided here as a non-goal; landed separately 2026-11-19 — `api_keys.capabilities`, middleware-enforced).
- A dedicated server-side `relabel` verb — amend with `metadata.scopes` remains the mechanism (docs/api.md already flags it); the CLI `relabel fact` verb is client-side sugar over amend, so D2 gating applies unchanged.
- Schema registration (`register schema` — designed in RFC-005, server "not yet implemented").
- Backup/export verbs (raw `tar` per docs/deployment.md remains fine for a single-box deployment).
- Enforcement-flag flip from the UI — it is an env/deploy act (§2.1 #5); the console displays state and instructions only.

## 4. Operator Console (memogarden-api, auth_views template family)

Follows the established page pattern exactly: Tailwind CDN shell templates, JWT held in `localStorage` (`access_token`), all data via `fetch` + `Authorization: Bearer`, client-side redirect to `/login` when absent. No server-side session, no new service (service-minimalism), no build pipeline. mg-project-studio remains the semantic browsing/observe surface; the console is the governance + verification surface.

### 4.1 IA — six screens (no dashboard)

The operator rejected an Overview/dashboard as premature: without a felt sense of what needs focusing on, a dashboard invents priorities. Nothing is orphaned by its absence — needs-reauth and run history live in Providers, the triage backlog is the sidebar badge, the enforcement banner is global. The console opens on **Triage**.

| Route | Screen | Backed by |
|---|---|---|
| `/console/triage` (also `/console` redirect target) | Triage review — queue, tap-row item detail modal, per-item and bulk relabel into destination scopes, recent relabels | `GET /api/v1/triage` + `/mg amend` + `/mg query` type=Action (op=amend) |
| `/console/scopes` | Scopes & grants — scope list + mint/delete, grants table + issue/revoke, provider scope policies, enforcement state | `GET /api/v1/scopes/overview` + `POST /mg create` + `/mg forget` + `POST/DELETE /grants` + `PUT /providers/<name>/scope-policy` |
| `/console/identity` | Keys & agents — links/extends the existing `/api-keys`, `/api-keys/new`, `/agents/` pages (which keep working) with grants and unbound-key warnings | existing endpoints |
| `/console/providers` | Providers — registry, accounts (status/config overrides/run buttons/re-auth instructions), run history | `GET /api/v1/providers*` + `GET /api/v1/providers/<name>/runs` + account PATCH/verify/run |
| `/console/occurrences` | Occurrence review — keyboard-fast queue (`o` = mark occurred, `d` = defer) | `GET /api/v1/events/pending-review` + `POST /api/v1/events/<uuid>/mark-occurred` |
| `/console/audit` | Action audit — attributed Action/ActionResult timeline with filters | `/mg query` type=Action |

Console routes live in a new `console_bp` (api/middleware/console.py) — HTML shells only, no data, auth client-side like every existing page. Badges in the UI: **OP** = operator-only (JWT-gated by design); **DELE** = delegable to a bound agent, operator verifies (naming per operator decision — "DEL" read as DELETE).

### 4.2 "Did not occur" is not representable in v1

mark-occurred sets `occurred_at` (RFC-014 §6.1); there is no dismissal path for events that did not happen — they simply stay unmarked. The occurrence queue therefore offers **mark occurred / defer** only; unmarked events age out of the review window (default 30 days). A `did_not_occur` stamp is future work (needs an RFC-014 amendment, not a console workaround).

## 5. Operator CLI (memogarden-client)

Verb-first `memogarden <verb> <noun>` per the accepted grammar. `register` stays the shared mint verb; `list/show/verify/run/remove` extend their account usage; `edit` is added for PATCH semantics; bare verbs are reserved for the session/auth family (consistent with install-cli.sh routing: bare verb → server lifecycle, verb+noun → client — `run providers` cannot collide with bare `run`).

### 5.1 Tier 0 — session path (the unblocker)

`AuthManager` gains `"jwt"` transport (`Authorization: Bearer`, RFC-001-aligned: keys stay X-API-Key-only). Session custody follows issuance: JWT stored 0600 at `~/.config/memogarden/operator-session.json` (`{url, access_token, expires_at}`), `MEMOGARDEN_JWT` env override. Each verb declares its required credential class; governed verbs fail fast with "requires `memogarden login`" — no lazy minting (RFC-010 invariant).

| Command | Endpoint | Auth |
|---|---|---|
| `memogarden login` | `POST /auth/login` | — |
| `memogarden logout` | local session clear | — |
| `memogarden whoami` | `GET /auth/me` | any |
| `memogarden change-password` | `POST /auth/change-password` | JWT |

### 5.2 Tier 1 — identity & authority (JWT)

`register agent --label` · `list agents` · `register key --label [--agent]` · `edit key --bind/--unbind` · `remove key` · `list keys` · `register scope --name` · `list scopes` · `remove scope` · `register grant --key --scope… [--expires]` · `list grants [--key]` · `remove grant` — endpoints per RFC-010/RFC-011 (`/agents`, `/api-keys`, `/grants`, `/mg create|forget` Scope).

### 5.3 Tier 2 — provider operations (API-key OK)

`list providers` · `show provider` · `run provider NAME` · `run providers --due` · `edit account [--status] [--interval] [--config k=v]` · `list runs --provider [--account] [--status] [--limit]` · `show run` — plus the existing `register/list/show/verify/run/remove account`, `register provider`, `import opml`.

### 5.4 Tier 3 — review & audit flows

`enter SCOPE` / `leave` · `list triage [--source]` (sugar over the new triage read) · `relabel fact --id --add [--remove]` (sugar over `/mg amend`) · `list actions [--actor] [--actor-type] [--op] [--result] [--since]` · `query --type --filter k=v --limit` (generic `/mg query` passthrough) · `track UUID`.

### 5.5 Tier 4 — planned, not built here

`register schema` / `list schemas` (RFC-005-designed; server unimplemented); `register key --capabilities runs` — the E3 capability model landed server-side 2026-11-19 (RFC-013 register slice 2); the CLI flag remains open.

## 6. Decisions of record

1. **No Overview/dashboard** — operator decision 2026-11-19 ("premature — I don't have a feel for what needs focusing on yet"); console opens on Triage.
2. **DELE badge naming** — operator decision (DEL confusable with DELETE).
3. **Relabel = client-side sugar over amend**, no server verb.
4. **Console reads are JWT-only** operator conveniences; run-history reads are any-principal (matches provider GET).
5. **No pause/resume CLI verbs** — `edit account --status`; **no backup verb** — tar.
6. **Console follows the localStorage-JWT page pattern** — no server sessions, no new service, no SPA/build pipeline.
7. **"Did not occur" deferred** — needs an RFC-014 amendment; queue is mark/defer only.
8. **Provider-module registration is NOT tightened to JWT here** (RFC-012 ride-along stands); noted as a candidate follow-up.

## 7. Rollout

1. api: feat branch → suites green (run_tests.sh; known pre-existing fail `test_manual_provider_run_starts_all_active_accounts` is not a regression) → merge → `systemctl --user restart memogarden` → health + live-verify new endpoints.
2. client: feat branch → suites green → merge (CLI is uv-editable on PATH — live immediately).
3. docs: `docs/api.md` gains the new endpoints + refreshed CLI block (root repo).
4. Enforcement flag stays OFF; nothing in this RFC flips advisory gates.

## 8. Addendum — single-GUI consolidation (2026-11-19, same day as v1 ship)

The operator reviewed the result and found **two GUIs** sharing one domain: the console (read-only identity overview) and the pre-RFC-010 pages it linked to (`/api-keys`, `/api-keys/new`, `/agents/`, `/settings`). Decision: one GUI — the console absorbs everything; the legacy pages retire.

1. **`/console/identity` is now full management** (was deliberately read-only in §4.1): agent mint form, key mint form (name / optional expiry / bind-at-creation) with the one-time reveal modal, per-key bind/unbind + revoke, unbound-key banner. Same section/table styling as `/console/scopes`; actions update in place. The §4.1 row "links/extends the existing … pages (which keep working)" is superseded — those pages are gone.
2. **`/console/settings` (seventh screen)** — operator profile (`GET /auth/me`) + change-password form (`POST /auth/change-password`, §3.1). The endpoint had no UI anywhere; the read-only `/settings` page it absorbs is retired.
3. **Retirement with soft landings** — the four legacy page routes + templates are deleted; `console_bp` serves 302s: `/api-keys`, `/api-keys/new`, `/agents/` → `/console/identity`; `/settings` → `/console/settings`; `/` → `/console` redirect (was: render login directly). `/login` stays (console's own auth entry, console-branded, post-login → `/console/triage`); `/admin/register` stays (localhost first-run bootstrap). JSON contracts (`GET /agents`, `GET /api-keys/`, PATCH/DELETE `/api-keys/<id>`) untouched.
4. **IA note** — this grows §4.1's six screens to seven and inverts the identity screen's role, but keeps the no-dashboard decision untouched.

---
**Last updated**: 2026-11-19 (addendum §8: single-GUI consolidation)
