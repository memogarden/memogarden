---
name: memogarden-development
description: Development environment setup, running the API server, architectural constraints, anti-patterns to avoid, shell command best practices, and working with per-project plans. Use when doing ANY development work on MemoGarden.
---

# MemoGarden Development

## Starting Work on a Task

When beginning work on any MemoGarden task:

1. **Read the context**
   ```bash
   cat plan/memogarden_prd_v0_11_0.md  # Understand platform architecture
   cat plan/budget_prd.md              # Budget app (paused since Jan 2026)
   ```
   The core implementation plan (`plan/memogarden-implementation-plan.md`) is
   **RETIRED (2026-10-08)** — historical record only. For current state, read
   each repo's git history and its own per-project plan (`mg-project-studio`,
   `mars`, `mush`).

2. **Navigate to the repo you are changing** — MemoGarden is a multi-repo
   workspace; every sibling has its own git history. Check `pwd` before any
   git command.

3. **Check current state + for other agents**
   ```bash
   git status
   git log --oneline -10
   awt list --repo <repo>   # any other agent's worktree claims here?
   ```

## Agent Worktrees (mandatory protocol)

Default to a registered awt worktree for multi-step code work — create it
BEFORE writing code. **The main checkouts are production**: the server venv
(`memogarden-api/.venv`) is all pip-editables pointing at the main working
trees, so edits there ship to production on the next service restart. Do
multi-step work in a worktree, merge to main only after tests pass, and never
restart `memogarden.service` as a side effect of development.

```bash
git worktree add ../<repo>-wt-<slug> -b <branch-per-repo-convention>
awt register --path . --task "<task>" --agent zcode
awt heartbeat --path .        # at milestones for long work
# when done: awt deregister --path . --unlock; remove your own worktree
```

Branch names follow each repo's own conventions (`feat/…`, `fix/…`, `ci/…`,
`chore/…`). Never delete a worktree you did not create — `awt sweep` →
`awt ui` → user-approved `awt remove`.

## Setting Up the Development Environment

### Install Poetry (if not present)

```bash
pip install poetry
poetry self add poetry-plugin-shell
```

### Install dependencies

```bash
cd memogarden-system      # or memogarden-api / memogarden-client / mush
poetry install
```

### 🚫 NEVER Use These Commands

```bash
# ❌ DON'T - Bypasses Poetry's dependency management
pip install package-name
python -m pip install package-name
pip3 install package-name
sudo pip install package-name
```

**Why**: Using `pip` directly bypasses Poetry's dependency lock file, can
cause version conflicts, and breaks the reproducible development environment.

**Exception — the server venv (`memogarden-api/.venv`) is all-editables by
design** (see Production Server Ops below): providers, hacm, system, utils,
api are `pip install -e` into it. Never snapshot-install anything into that
venv from a file:// path or PyPI — snapshot installs go stale silently (the
2026-11-20 gmail-ingest-worker massacre). Keep `pip check` clean in that
venv; a broken `pip check` means someone snapshot-installed.

### Known gotcha: ModuleNotFoundError at conftest

If `run_tests.sh` fails with ModuleNotFoundError at conftest imports (path
deps missing), the repo's poetry venv is stale or was created outside the
repo dir. Fix: run `poetry install` from the repo's own directory (not a
worktree without its own `.venv`) and rerun.

### Checking Installed Packages

```bash
poetry show
poetry show --tree
poetry show --outdated
```

### Working Directory Reminder

**Always verify your current directory before running commands:**
- **memogarden-system work**: `memogarden-system/` (for `./run_tests.sh`)
- **memogarden-api work**: `memogarden-api/`
- **memogarden-client work**: `memogarden-client/`
- **mush work**: `mush/` (own pyproject: ruff + pyright + pytest)

Most Poetry commands should be run from the relevant package directory.
Root-level scripts live in `scripts/` (`install-user-service.sh`,
`install-cli.sh`, `deploy-memogarden.sh`, `lint.sh`, `migrate.sh`,
`init-db.sh`) — there is no root test runner; each repo ships its own
`run_tests.sh`.

## Running the API Server Locally

### Using the convenience script (recommended)

```bash
./scripts/run.sh
```

### Manual startup

```bash
cd memogarden-api
poetry run flask --app api.main run --debug
```

The server runs at http://localhost:5000

**Note**: MemoGarden Core uses Flask (not FastAPI). There are no
auto-generated API docs.

**Caution**: the machine you develop on IS the production host. A dev server
on :5000 is fine, but never point probes that write to `get_db_path()` at the
real data dir without `MEMOGARDEN_DATA_DIR` set — it resolves near-cwd and
will silently create a stray `soil.db`.

## Running Tests

### ⚠️ STANDARD WAY

**IMPORTANT:** Always use the standardized `run_tests.sh` script for test
execution in the api/system/client repos. It ensures the correct Poetry
environment, works from any directory, and provides grep-able output.

```bash
# MemoGarden API Tests
./memogarden-api/run_tests.sh

# MemoGarden System Tests
./memogarden-system/run_tests.sh

# MemoGarden Client Tests
./memogarden-client/run_tests.sh

# With pytest arguments (passed through)
./memogarden-api/run_tests.sh -xvs
./memogarden-api/run_tests.sh tests/test_specific.py
./memogarden-api/run_tests.sh --cov=api --cov-report=html

# Get summary only (for agents - avoids full test output in context)
./memogarden-api/run_tests.sh --tb=no -q 2>&1 | tail -n 7
```

**Format options for agents:** `--format=markdown` (or `--format=md`) is
easier to parse; `--format=plaintext` is plain.

### mush (not covered by run_tests.sh)

mush has its own gates, run all four before pushing:

```bash
cd mush
poetry run ruff check . && poetry run ruff format --check .
poetry run pyright
poetry run pytest tests/ -q
```

### Specific test commands

**API Tests:**
```bash
./memogarden-api/run_tests.sh tests/test_semantic_api.py
./memogarden-api/run_tests.sh tests/test_context.py::test_enter_scope_adds_to_active_set -xvs
```

**System Tests:**
```bash
./memogarden-system/run_tests.sh tests/test_core.py
./memogarden-system/run_tests.sh tests/test_core.py::test_entity_create -xvs
```

For testing philosophy and debugging failures, use the **memogarden-testing**
skill.

## Running E2E Tests (Project Studio)

Project Studio has browser-based E2E tests that verify the frontend works
correctly with the API.

```bash
cd mg-project-studio
./run_e2e.sh

# Specific test file / headed mode
./run_e2e.sh e2e.spec.ts
./run_e2e.sh --headed
```

**Prerequisites:**
- API server running on http://localhost:5000
- Vite dev server running on http://localhost:5173 (`cd mg-project-studio && npm run dev`)

**Common E2E Issues:**
- "Playwright Test did not expect test.describe()" → wrong directory
- Use `./run_e2e.sh` which automatically changes to the correct directory
- Never run `npx playwright test` from the root directory

## Working With Plans

- `plan/memogarden-implementation-plan.md` — RETIRED 2026-10-08 (historical)
- Punch lists that ARE current live per-topic: `plan/rfc_011_implementation.md`
  (scope/grants — §"status" markers per item), per-project plans in
  `mg-project-studio/`, `mars/`, `mush/`.
- RFCs: `plan/rfc_*.md` are the design record; when an RFC item ships, update
  the punch list AND the GitHub issue in the same session.

### Close the loop on issues (lesson from the 2026-11-21 sweep)

Auto-close keywords in commit messages are unreliable — a direct-to-main
commit saying "closes #N" closed 1 of 3 referenced issues on GitHub; a
squash-merge branch left its issue open; and several fixes landed on main
with their issues never updated (found: api#46/52/61/64, mush#5/6 all
fixed-in-main-but-open). After every push that resolves an issue:
1. `gh issue view <N>` and confirm it actually closed — comment with the
   commit hash if not, then close it yourself.
2. When filing sweep findings, check "is this already fixed on main?" before
   writing fixes: `git log --oneline -i --grep "<topic>"` first.

## Production Server Ops (js-xiaomi, user units)

The production server is `memogarden.service` (systemd **user** unit,
gunicorn 127.0.0.1:8600, single sync worker), fed by
`memogarden-ingest.timer` hourly. **This dev machine IS that host**: the
server venv is `memogarden-api/.venv` with everything as pip editables
(providers gmail/mbox/tada/rss/gcal/sessions, hacm, system, utils, api).
Worktree == production: **deploy = merge to main + `systemctl --user
restart memogarden`** — never restart as a side effect of development; leave
the restart to the operator unless the task is the deploy itself.

Learned 2026-11-20 (gmail-ingest-worker-massacre, sysinfo incidents/):

- **Never `pip install` a provider from a file:// path or PyPI into the
  server venv** — snapshot installs go stale silently (the 2026-11-20
  massacre: fix committed 11-14, production still ran the buggy 0.1.0 base
  because the snapshot was never reinstalled). `pip check` must stay clean;
  a broken pip check means someone snapshot-installed.
- **Probe the installed path, not a hand-copied query**: run
  `python -c "import pkg; print(pkg.__file__)"` in the SERVER venv before
  trusting what code production runs. (With editables they match the
  worktree — but verify, don't assume; the venv is Python 3.14.)
- **"interrupted by server restart" in provider run rows is the
  `cleanup_orphaned_runs` sweep signature, NOT proof a restart happened.**
  Check journal Start/Stop lines for real restarts.
- **Stagger manual provider runs** — the heartbeat staggers accounts;
  batch-firing all accounts at once stacks RSS spikes (HACM unlock ≈ 1.17 GB
  transient per decrypt, serialized process-wide) and can breach
  MemoryHigh=2.5G / MemoryMax=3G / MemorySwapMax=512M → gunicorn 30s
  WORKER-TIMEOUT SIGKILL.
- **WORKER-TIMEOUT kills fire no SIGABRT** — the `worker_abort` faulthandler
  tripwire does NOT capture them; gunicorn just SIGKILLs. In-worker
  `faulthandler.dump_traceback_later` is armed for future freeze capture.
- **gunicorn stderr/stdout live in `~/.local/state/memogarden/logs/
  {error,access}.log`** — `journalctl --user -u memogarden` carries only
  systemd's own lines.
- **Async daemons that install SIGTERM handlers can hang past `kill`** when
  their graceful-shutdown path is incomplete (mush DaemonServer pre-M2:
  stop() hung while a client was connected). When a process ignores
  `timeout`/SIGTERM, get a stack (`faulthandler.dump_traceback_later`) before
  reaching for kill -9.
- Never batch-fire probes that write to `get_db_path()` without
  `MEMOGARDEN_DATA_DIR` set — it resolves near-cwd and will silently create
  a stray `soil.db`.

## Critical Architectural Constraints

### 🚫 What NOT to Do

1. **DO NOT use SQLAlchemy or any ORM**
   - Use raw SQL queries with parameterized statements
   - SQLite schema (`schemas/sql/schema.sql` in the root repo) is the source
     of truth
   - Pydantic is ONLY for API request/response validation

2. **DO NOT add heavy dependencies**
   - Keep the dependency list minimal
   - Question every new package addition
   - Prefer stdlib when possible (e.g., sqlite3 over aiosqlite)

3. **DO NOT use PostgreSQL or other databases**
   - SQLite only (lightweight, portable, minimal setup)
   - Perfect for personal use and Raspberry Pi deployment

4. **DO NOT use async/await unless truly needed**
   - This is a personal system with low traffic
   - Synchronous code is simpler, more deterministic, easier to debug
   - Use Flask (not FastAPI) and built-in sqlite3 (not aiosqlite)
   - (mush is the deliberate exception — a PTY daemon is inherently async)

5. **DO NOT skip testing**
   - Write tests alongside features
   - Use pytest with pytest-flask; in-memory/per-test temp SQLite in tests

6. **DO NOT over-engineer**
   - Build what's needed for the current step
   - Simple > clever

### ✅ What TO Do

1. **Use centralized utilities from memogarden-utils (no loose datetime/uuid
   imports)** — the `memogarden-utils` repo is the SOLE source of truth for
   shared utilities; the root repo no longer tracks copies:
   - **Date/time**: `from utils import isodatetime`
     - `isodatetime.now()` - Current UTC as ISO string
     - `isodatetime.now_unix()` - Unix timestamp (for JWT)
     - `isodatetime.to_timestamp(dt)` / `isodatetime.to_datetime(ts)`
   - **UUIDs**: `from utils import uid`
     - `uid.generate_uuid()` - UUID v4 (ONLY place that imports uuid4)
     - Prefixes: `entity_` for Core Entities, `item_` for Soil Items;
       the DB stores plain UUIDs, prefixes are added at the API layer
   - **Recurrence**: `from utils import recurrence`
     - `recurrence.validate_rrule()` / `generate_occurrences()` /
       `get_next_occurrence()`
     - All python-dateutil imports confined to this module
   - Also in utils: `hash_chain`, `attachments` (AttachmentStore), `config`
   - **Note**: there is no `schema.types` domain-types module — schemas live
     as YAML/SQL in the root `schemas/` dir; Pydantic models live in
     `api/schemas/`

2. **Use UTC timestamps everywhere**
   - Store as ISO 8601 text in SQLite: `2025-12-22T10:30:00Z`
   - Transaction dates are DATE only: `2025-12-22`
   - Never use local timezones in storage
   - All datetime operations go through `isodatetime`

3. **Write raw SQL queries**
   - Parameterized queries to prevent SQL injection

4. **Confine external dependencies to single modules**
   - Create abstraction layers for complex third-party libraries
   - Only one module imports the external package directly
   - Example: JWT tokens (only `auth/token.py` imports PyJWT)

5. **Follow the relevant project's plan**
   - Check the project's own plan/roadmap before starting work
   - Don't jump ahead to future steps

6. **Test everything**
   - Write tests before or alongside code
   - Use `isodatetime` for current time assertions in tests

7. **Document as you go**
   - Add docstrings to all functions
   - Keep RFC punch lists + GitHub issues current (see Close the loop)

8. **Trust boundaries are operator-governed (RFC-010 D1 / RFC-015)**
   - Registering provider code, issuing identities/API keys, scope_policy
     writes = operator JWT-gated endpoints
   - API keys never mint credentials or reconfigure trust

## Shell Command Best Practices

### Background Execution

**Use `run_in_background=True` instead of `&` and `2>&1`:**

```python
# ❌ AVOID - Triggers approval due to 2>&1 and &
Bash(command="poetry run flask --app api.main run --debug 2>&1 &")

# ✅ PREFERRED - Use run_in_background parameter
Bash(
    command="poetry run flask --app api.main run --debug",
    run_in_background=True
)
```

### Command Chaining

**Use `&&` for dependent commands; `;` for independent ones:**

```bash
cd memogarden-api && poetry install && ./run_tests.sh
mkdir -p data ; ./run_tests.sh
```

## Anti-Patterns to Avoid

❌ Don't use `pip install` (except the documented all-editables server venv
   convention) — use Poetry (`poetry add`)
❌ Don't create ORM models when schema exists
❌ Don't add dependencies without discussion
❌ Don't skip tests "for now"
❌ Don't use local time zones
❌ Don't over-engineer for future needs
❌ Don't ignore the project's plan/roadmap
❌ Don't batch multiple unrelated changes
❌ Don't use `2>&1` in shell commands (triggers approval workflow
   unnecessarily)
❌ Don't import `datetime` directly in business logic (use `isodatetime`)
❌ Don't import `uuid4` directly (use `uid.generate_uuid()`)
❌ Don't scatter external library imports across modules (confine to one
   place)
❌ Don't commit on main checkouts directly for multi-step work — use an awt
   worktree; main is production
❌ Don't trust auto-close keywords — verify the GitHub issue actually closed

✅ Do use Poetry for package management
✅ Do write raw SQL queries
✅ Do keep dependencies minimal
✅ Do write tests alongside code
✅ Do use UTC everywhere
✅ Do build for current needs
✅ Do follow the plan
✅ Do make focused, atomic commits
✅ Do use `run_in_background=True` for long-running server processes
✅ Do use centralized utilities (`isodatetime`, `uid`, `recurrence`)
✅ Do confine external dependencies to single modules
✅ Do run the full gate (ruff + format + pyright + pytest) before merging

## Getting Help

If stuck:
1. Re-read the PRD (`plan/memogarden_prd_v0_11_0.md`) for context
2. Check the workspace README + the relevant project's own plan/docs
3. Look at existing code patterns
4. Use appropriate skill (memogarden-testing, memogarden-debugging,
   memogarden-api-endpoint, memogarden-schema)
5. Ask the user with specific questions

### Key References

- **[docs/repositories.md](../../../docs/repositories.md)** - Repository
  structure and module breakdowns
- **[docs/deployment.md](../../../docs/deployment.md)** - Deployment guide
- **[docs/configuration.md](../../../docs/configuration.md)** - Configuration
  reference
- **[plan/memogarden_prd_v0_11_0.md](../../../plan/memogarden_prd_v0_11_0.md)**
  - Complete platform architecture
- **`plan/future/`** - schema-extension-design, migration-mechanism,
  soil-design (deferred features; consult before related work)

**Important:** Soil implementation is DEFERRED until agent workflows are
added. For Budget MVP: no Soil Items, no schema snapshots, no delta
archival, Entity-based features only.
