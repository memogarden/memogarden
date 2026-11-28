# MemoGarden Repository Structure

**Last Updated:** 2026-11-19
**Status:** Session 14+

Complete reference for MemoGarden repositories and their structure.

## Core Repositories

These repositories form the production system. (Additional sibling repositories —
`memogarden-client`, `memogarden-utils`, `mg-project-studio`, `mars`, `mush` — are
described in the root [README](/README.md).)

---

### 1. Root Repository (`/`)

**Purpose:** Planning documents, development scripts, agent configuration

**Contains:**
- `plan/` - Planning documents
  - `memogarden-implementation-plan.md` - Implementation roadmap (retired 2026-10-08 — historical)
  - `memogarden_prd_v0_11_0.md` - Platform PRD
  - `budget_prd.md` - Budget app requirements
  - `rfc_*.md` - RFC documents (RFC-001 through RFC-015)
- `providers/` - Ingestion providers (tracked in this repo)
  - `gmail-importer/` - Gmail ingestion provider (historyId incremental sync,
    progressive backfill, Provider Protocol implementation; published
    standalone as [memogarden-gmail-provider](https://github.com/memogarden/memogarden-gmail-provider))
  - `mbox-importer/` - Google Takeout mbox importer (published standalone as
    [memogarden-mbox-importer](https://github.com/memogarden/memogarden-mbox-importer))
  - `tada-importer/` - tada task-history importer (day-walk over tada's HTTP
    command bridge → generic Task facts + linked Note facts; see
    `schemas/task.yaml`)
- `gmail-oauth/` - Gmail OAuth 2.0 flow (tokens stored in HACM)
- `scripts/` - Development automation
  - `run.sh` - Start API development server
  - `install-user-service.sh` - Install user-session systemd units (server + ingestion heartbeat)
  - `test_entrypoint.sh` - Centralized test runner (used by each project's `run_tests.sh`)
  - `lint.sh` - Run ruff linter
  - `debug-memogarden.sh` - RPi installation diagnostics
  - `deploy-memogarden.sh` - Deployment script
  - `pre-commit` - Pre-commit hook
- `docs/` - User and developer documentation
  - `quickstart.md` - Getting started guide
  - `deployment.md` - Deployment guide
  - `configuration.md` - Configuration reference
  - `repositories.md` - This file
- `.claude/` - Claude Code agent configuration
  - `skills/` - Task-specific skills
  - `agents/` - Subagents for code review
- `AGENTS.md` / `CLAUDE.md` - Agent guide (symlink)

**Git:** Root repository
**Commits affect:** docs, plans, scripts, .claude/

---

### 2. System Repository (`memogarden-system/`)

**Purpose:** Core library containing Soil and Core layers

**Structure:**
```
memogarden-system/
├── system/
│   ├── __init__.py
│   ├── config.py              # Configuration management
│   ├── exceptions.py          # MemoGarden exceptions
│   ├── schemas.py            # Schema loading utilities
│   ├── core/                # Core database operations
│   │   ├── __init__.py      # Core.get_core()
│   │   ├── entity.py        # Entity registry operations
│   │   ├── transaction.py   # Transaction operations
│   │   ├── recurrence.py    # Recurrence operations
│   │   ├── relation.py      # User relations (RFC-002)
│   │   └── context.py      # Context frames (RFC-003)
│   ├── soil/                # Soil database operations
│   │   ├── __init__.py
│   │   ├── artifact.py       # Artifact operations
│   │   └── ...
│   ├── utils/               # Shared utilities
│   │   ├── uid.py          # UUID utilities
│   │   ├── isodatetime.py  # Timestamp utilities
│   │   ├── hash_chain.py   # Hash computation
│   │   └── secret.py      # Secret generation
│   ├── schemas/             # Schema source (schemas-v1 unification, 2026-11-19)
│   │   ├── memogarden/      #   SUBMODULE ← memogarden/memogarden (schema docs
│   │   │                    #   + sql — the single source of truth; the former
│   │   │                    #   package copies are deleted)
│   │   └── sql/             #   package-side migrations (load-bearing:
│   │       └── migrations/  #   _run_migrations reads them package-relatively)
│   └── host/               # Host environment utilities
│       └── environment.py   # Path resolution (RFC-004)
├── tests/
│   ├── test_transaction_coordinator.py
│   ├── test_schemas.py
│   ├── test_cross_db_experiment.py
│   └── test_rfc004_deployment.py
├── pyproject.toml
└── README.md
```

**Git:** Separate repository
**Commits affect:** system/
**Dependencies:** None (base library)

**Schema submodule:** `system/schemas/memogarden` is a git submodule pinning
`memogarden/memogarden` (tag `schemas-v1`, bumped to `schemas-v2` on
2026-11-19) — schema docs AND sql live there now; the old package copies were
retired (memogarden/memogarden PR #7, memogarden-system PR #4). Accessor
constants and fallback chains resolve mount-first. **The deploy protocol
includes `git submodule update --init`** after pulling — a missing mount
fails schema loading at boot.

---

### 3. API Repository (`memogarden-api/`)

**Purpose:** Flask REST API and Semantic API

**Structure:**
```
memogarden-api/
├── api/
│   ├── __init__.py
│   ├── main.py              # Flask app factory
│   ├── config.py            # API configuration
│   ├── exceptions.py         # API exceptions
│   ├── validation.py        # Request validation
│   ├── semantic.py         # Semantic API (/mg endpoint)
│   ├── schemas/            # Pydantic schemas
│   ├── v1/                # REST API v1
│   │   ├── __init__.py
│   │   ├── core/           # Core endpoints
│   │   │   ├── transactions.py
│   │   │   ├── recurrences.py
│   │   │   ├── relations.py
│   │   │   └── context.py
│   │   └── soil/          # Soil endpoints
│   │       └── artifacts.py
│   ├── handlers/           # Semantic API handlers
│   ├── middleware/         # Auth, decorators
│   └── templates/         # HTML templates
├── tests/
│   ├── conftest.py        # Test fixtures
│   ├── test_transactions.py
│   ├── test_recurrences.py
│   ├── test_auth.py
│   ├── test_semantic_api.py
│   ├── test_context.py
│   ├── test_relations_bundle.py
│   ├── test_user_relations.py
│   ├── test_search.py
│   ├── test_track.py
│   ├── test_audit_facts.py
│   ├── test_path_resolution.py
│   ├── test_health_status.py
│   └── README.md
├── pyproject.toml
├── gunicorn.conf.py       # Production server config
└── README.md
```

**Git:** Separate repository
**Commits affect:** api/, tests/
**Dependencies:** memogarden-system

---

## Related Repositories

These are related to MemoGarden but not part of the core system.

### `memogarden-search/`

**Purpose:** The semantic search plane and the opt-in rerank plane (api#95) — the model-layer service behind the `search` verb's `semantic` / `hybrid` strategies and the console Items tab. Query-time inference never runs inside the API process: the API talks to this layer over a unix socket (`api/search_daemon.py`).

**Status:** Live — socket-activated (`memogarden-embed.socket` / `memogarden-embed.service` in `deploy/`), idle self-exit after 600s; woken by searches and by the embed-on-ingest ping after each provider run
**Contains:**
- `embed_daemon.py` - lazy search daemon: loads bge-small + the chunked vector index once, answers `search` / `rerank` / `ping` ops, and runs a change_log-driven catch-up on every wake
- `build_search_index.py` - index builder (chunking, CLS pooling, hash-keyed chunk cache so rebuilds embed only new/changed text)
- `index_store.py` - index format (mmap-able `search_index.npy` + ids/meta JSON + watermark) and the catch-up planner
- `reranker.py` - cross-encoder rerank plane (int8, lazy-loaded; opt-in)
- `tools/` - `golden_replay.py` + `golden_set.json` (golden-set replay), `smoke_compare.py`
- `deploy/` - the systemd user units
- `tests/` - no-torch tests (`.venv/bin/python -m pytest tests/`)

**Dependencies:** consumes the trigger-maintained `change_log` feed from memogarden-system; called by memogarden-api

### `memogarden-docling/`

**Purpose:** The document-ingestion worker — PDF in, structured JSON (DoclingDocument) + markdown out — the extraction stage of the statement pipeline (root#14). Content-agnostic by design: no Soil, no Core, no MemoGarden paths; the MemoGarden-aware half is `providers/docling-extractor/` in the root repo.

**Status:** Live (trial 2026-11-26) — socket-activated (`memogarden-docling.socket` / `memogarden-docling.service`, idle self-exit 900 s, `MemoryMax=3500M`); `CUDA_VISIBLE_DEVICES` must stay EMPTY (the MX150 is visible to torch via the GLM-OCR driver but pip torch lacks sm_61 kernels)
**Contains:**
- `docling_daemon.py` - unix-socket daemon: one JSON request per connection, model stays warm, honors systemd socket activation (LISTEN_FDS fd 3)
- `pyproject.toml` - uv-pinned python 3.12 venv, docling 2.153.0 (layout-engine comparison verdict: 8.15 s/page, clean born-digital rows)
- `README.md` - protocol + ops

**Dependencies:** none on MemoGarden packages; called over the socket by `providers/docling-extractor/ingest_documents.py` (root repo), which watches `~/Documents` on `memogarden-docling-ingest.timer` (minutely): sha256 → AttachmentStore copy → Triage-scoped `Attachment` fact → `ingested/` mirror → worker conversion → Core `Artifact` (markdown, `data.scopes`, `data.source.attachment_uuid` linkage, `category="unknown"`). See `~/sysinfo/incidents/2026-11-26-memogarden-docling-documents-ingest.md`.

### `memogarden-readly/`

**Purpose:** readly — RSS reader PWA on MemoGarden (the first MemoGarden app)

**Status:** Deployed (2026-11-18) at `readly.ngjunsiang.me` (tada Cloudflare tunnel + Access; `readly.service`, port 7428)
**Contains:**
- `packages/server/` - `readly serve` (node:http): static host + JSON API over an in-memory Entry/Subscription cache (syncs from `POST /mg`) + app-local read state (`~/.local/share/readly/state.json`)
- `packages/web/` - PWA UI (Vite + React 19 + TanStack Query 5, DOMPurify reader, sw.js with Access session recovery)
- `scripts/` - systemd unit mirror + installer

**Note:** read state is deliberately app-local; MemoGarden will eventually record URL visits as facts, subsuming it (see `~/sysinfo/incidents/2026-11-18-readly-pwa.md`).

### `memogarden-artilab/`

**Purpose:** artilab — artifact editor PWA (browse/search/inspect the docling-era Core Artifacts; relabel metadata via `/mg edit`; capture markdown artifacts)

**Status:** Deployed (2026-11-28) at `artilab.ngjunsiang.me` (tada Cloudflare tunnel + Access; `artilab.service`, port 7429)
**Contains:**
- `packages/server/` - `artilab serve` (node:http): static host + **transparent reverse proxy** (`/api/*`, `/mg`, `/auth/*` → 127.0.0.1:8600) — zero secrets; the operator JWT lives in the browser (artifact routes are JWT-only by design)
- `packages/web/` - PWA UI (Vite + React 19 + TanStack Query 5, marked + DOMPurify, hand-rolled 4-route routing, sw.js with Access session recovery)
- `scripts/` - systemd unit mirror + installer + icon generator

**Note:** content stays read-only (editing requires the Project Studio `commit_artifact` fragment protocol); scope edits stay in the console (cascade semantics). Details in `memogarden-artilab/HANDOVER.md` and `~/sysinfo/incidents/2026-11-28-artilab-deploy.md`.

### `app-budget/`

**Purpose:** Budget app (Flutter)

**Status:** Future development
**Platforms:** Web + Android

### `memogarden-devcd/`

**Purpose:** Continuous deployment infrastructure

**Contains:**
- `webhook/` - Flask webhook server
- `scripts/` - Installation and deployment
- `systemd/` - Service definitions
- `.github/workflows/` - GitHub Actions

**Use:** Automated deployment to Raspberry Pi

See [deployment.md](deployment.md) for details.

### `memogarden.github.io/`

**Purpose:** Documentation website

**Status:** Future
**Content:** Public documentation site

### `hacm/`

**Purpose:** Headless Agent Credential Manager

**Status:** Separate project
**Relation:** Manages credentials for MemoGarden and other services

---

## Path Conventions

### Repository Root

- **Development:** `$HOME/memogarden/` (e.g., `/home/user/memogarden/` or `/workspaces/memogarden/` in devcontainer)
- **Production (RPi):** `/opt/memogarden/`
- **User:** `$HOME/memogarden/`

### Relative Imports

**In memogarden-system:**
```python
from system.core import get_core
from system.config import settings
from system.host.environment import get_db_path
```

**In memogarden-api:**
```python
from system.core import get_core  # from memogarden-system
from api.v1.core import transactions_bp
from api.semantic import semantic_bp
```

---

## Database Paths (RFC-004)

By deployment verb:

| Verb | Soil DB | Core DB |
|------|----------|----------|
| `serve` | `/var/lib/memogarden/soil.db` | `/var/lib/memogarden/core.db` |
| `run` | `~/.local/share/memogarden/soil.db` | `~/.local/share/memogarden/core.db` |
| `deploy` | `/data/soil.db` | `/data/core.db` |

---

## Common Operations

### Running Tests

**Use the standardized `run_tests.sh` script for all test execution.**

```bash
# System package tests
cd memogarden-system
./run_tests.sh

# API package tests
cd memogarden-api
./run_tests.sh

# Client package tests
cd memogarden-client
./run_tests.sh
```

### Starting Development Server

```bash
# From root
./scripts/run.sh

# Or directly
cd memogarden-api
poetry run flask run
```

### Git Workflow

```bash
# Always verify repository before committing
pwd                    # Check directory
git status --short      # Check what changes

# Commit in correct repository
cd memogarden-api      # or memogarden-system
git add .
git commit -m "message"
```

---

## See Also

- [AGENTS.md](/AGENTS.md) - Quick reference for AI agents
- [quickstart.md](quickstart.md) - Getting started
- [deployment.md](deployment.md) - Deployment guide
- [configuration.md](configuration.md) - Configuration reference
