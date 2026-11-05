# MemoGarden

**MemoGarden** is a personal memory system for financial transactions. It's not traditional
budgeting software — it's a belief-based transaction capture and reconciliation system
designed for both human users and AI agents.

**Core philosophy:**

1. **Transactions Are Beliefs** — a transaction represents the user's understanding at the time of payment, not the bank's ledger
2. **Single Source of Truth** — all transactions flow through the MemoGarden Core API
3. **Mutable Snapshot, Immutable Memory** — current state can change, but all changes are logged via deltas
4. **Document-Centric Traceability** — transactions link to immutable artifacts in Soil (emails, invoices, statements)
5. **Agent-First Design** — humans and agents use the same APIs

## Workspace Map

This workspace contains multiple git repositories. See [AGENTS.md](AGENTS.md) for the
committing rules — every directory below has its own git history unless noted otherwise.

### Platform (the production system)

| Repository | What it is |
|-----------|------------|
| `memogarden-system/` | Core library: **Soil** (immutable facts) + **Core** (mutable beliefs). SQLite, raw SQL, no ORM |
| `memogarden-api/` | Flask HTTP API: Semantic API (`POST /mg`), REST v1, auth + API keys, SSE events |
| `memogarden-client/` | Python SDK (`mg_client`): Semantic API operations incl. artifact deltas, conversations, SSE |
| `memogarden-utils/` | Shared utilities: uid, hashing, config, recurrence, formatting |
| `schemas/` | Canonical data model: YAML, SQL schemas, JSON Schema types *(part of root repo)* |
| `providers/` | Email ingestion providers: `gmail-importer` (historyId incremental sync + progressive backfill; published standalone as [memogarden-gmail-provider](https://github.com/memogarden/memogarden-gmail-provider)) and `mbox-importer` (Google Takeout; published as [memogarden-mbox-importer](https://github.com/memogarden/memogarden-mbox-importer)) *(root repo)* |

### Apps & Agent Ecosystem (newer work)

| Repository | What it is |
|-----------|------------|
| `mg-project-studio/` | Svelte 5 web app (RFC-009 MemoGarden App): scope management, artifact delta editing, SSE observe mode |
| `mars/` | MARS (MemoGarden Agent Runtime System) — submodule; task-oriented agents whose memory lives in storage, not context |
| `mush/` | MUSH (Multi-User Session Host) — PTY daemon for safe human–agent shared terminal control |
| `hacm/` | Headless Agent Credential Manager — submodule; encrypted credential storage for headless agent loops |
| `gmail-oauth/` | Gmail OAuth 2.0 flow, stores refresh tokens in HACM *(root repo)* |
| `app-budget/` | Budget app (Flutter) — submodule; development paused since Jan 2026 |

### Infrastructure & Reference

| Directory | What it is |
|-----------|------------|
| `memogarden-devcd/` | Webhook-based CD for Raspberry Pi deployment |
| `memogarden.github.io/` | Documentation site |
| `letta/`, `letta-python/` | Vendored Letta (MemGPT) reference code (gitignored, read-only) |
| `claudecode-webui/`, `multi-agent-dev/`, `collection-claude-code-source-code/` | External tooling and reference archives |
| `shrub/` | Personal Logseq knowledge base (gitignored) |

## Documentation

- **[AGENTS.md](AGENTS.md)** — start here; agent guide with repo boundaries, skills, and workflows
- **[docs/](docs/)** — [quickstart](docs/quickstart.md), [API reference](docs/api.md),
  [deployment](docs/deployment.md), [configuration](docs/configuration.md),
  [architecture](docs/architecture.md), [troubleshooting](docs/troubleshooting.md)
- **[plan/](plan/)** — PRD (`memogarden_prd_v0_11_0.md`), RFC-001 through RFC-009, implementation plans
- **Per-project docs** — `mars/`, `mush/`, and `mg-project-studio/` have their own docs and plans

> ⚠️ The `docs/` folder was written at "Session 15" (Feb 2026) and covers the core platform
> as of that date. Features added later — Scopes, artifact deltas, conversations, SSE — are
> documented in the RFCs and in the per-project docs instead.

## Status

- **Platform** (`memogarden-system` / `-api` / `-client`): stable; Gmail ingestion is live — the registered gmail provider runs hourly in the API server (historyId incremental sync + a fully-ingested progressive backfill); ~510 tests across the packages
- **Active development**: `mars` (agent runtime), `mush` (terminal sharing), `mg-project-studio`
- **Deployment**: user-session systemd units on the daily-driver laptop (`scripts/install-user-service.sh`), Raspberry Pi appliance via `memogarden-devcd` / `install.sh`; see [docs/deployment.md](docs/deployment.md)

## License

TBD
