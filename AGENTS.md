# MemoGarden - AI Agent Guide

This document provides context and guidelines for AI assistants (Claude, GPT, etc.) working on the MemoGarden project.

## Project Overview

**MemoGarden** is a personal memory system for financial transactions. It's not traditional budgeting software—it's a belief-based transaction capture and reconciliation system designed for both human users and AI agents.

### Core Philosophy

1. **Transactions Are Beliefs** - A transaction represents the user's understanding at the time of payment, not the bank's ledger
2. **Single Source of Truth** - All transactions flow through MemoGarden Core API
3. **Mutable Snapshot, Immutable Memory** - Current state can change, but all changes are logged via deltas
4. **Document-Centric Traceability** - Transactions link to immutable artifacts in Soil (emails, invoices, statements)
5. **Agent-First Design** - Humans and agents use the same APIs

## Repository Structure

⚠️ **CRITICAL:** MemoGarden is a **multi-repository workspace**. Always check which repository you're working in before committing changes.

### Sibling Repositories (own git history, gitignored by root)

1. **Root** (`/`) - Planning, docs, schemas, scripts, agent configuration, `providers/` (gmail + mbox ingestion providers), `gmail-oauth/`, published standalone repos `memogarden-gmail-provider` + `memogarden-mbox-importer`
2. **System** (`memogarden-system/`) - Core library (Soil + Core layers)
3. **API** (`memogarden-api/`) - Flask REST + Semantic API + SSE
4. **Client** (`memogarden-client/`) - Python SDK (`mg_client`) for the MemoGarden API
5. **Utils** (`memogarden-utils/`) - Shared utilities (uid, hashing, config, recurrence) — NOTE: also tracked in the root repo (files appear in both; treat root as source of truth for now)
6. **Project Studio** (`mg-project-studio/`) - Svelte web app (RFC-009 MemoGarden App)
7. **MUSH** (`mush/`) - Multi-User Session Host (PTY daemon for human-agent shared terminals)
8. **CD** (`memogarden-devcd/`) - Webhook-based deployment automation
9. `memogarden.github.io/` - Documentation site

### Submodules (pinned in root repo as gitlinks)

- `mars/` - MARS (MemoGarden Agent Runtime System): Python agent runtime with NiceGUI/Flutter GUIs
- `hacm/` - Headless Agent Credential Manager (encrypted credential store)
- `app-budget/` - Budget app (Flutter); developed through Jan 2026, currently paused
- `collection-claude-code-source-code/` - External reference archive

### Tracked Directories (part of the root repo)

- `gmail-oauth/` - Gmail OAuth flow, stores credentials in HACM
- `providers/` - Ingestion providers (`mbox-importer`, `gmail-importer` email ingestion; `tada-importer` task ingestion → Task facts, see `schemas/task.yaml`)
- `schemas/` - Canonical data model (YAML, SQL, JSON Schema)

**For detailed repository structure, file listings, and module breakdowns, see [docs/repositories.md](docs/repositories.md).**

### ⚠️ Git Repository Boundaries

**Every directory listed under "Sibling Repositories" has its own git history.** Submodules are pinned in the root repo: after committing inside a submodule, the root repo sees the pointer change.

1. **Root Repository** (`/`)
   - Contains: Planning documents, docs, schemas, automation scripts, agent skills, gmail-oauth
   - Git command: `git status` (from root directory)
   - Commits here affect: docs, plans, schemas, scripts, .claude/, gmail-oauth/

2. **API Repository** (`memogarden-api/`)
   - Contains: Flask application, Semantic API, SSE events, tests
   - Git command: `git status` (from memogarden-api directory)
   - Commits here affect: api/, tests/
   - **Separate from root repository!**

3. **System Repository** (`memogarden-system/`)
   - Contains: Database operations, business logic, exceptions
   - Git command: `git status` (from memogarden-system directory)
   - Commits here affect: system/, tests/
   - **Separate from root repository!**

The same applies to `memogarden-client/`, `memogarden-utils/`, `mg-project-studio/`, `mush/`, `memogarden-devcd/`, and `memogarden.github.io/`. Run `git status` inside whichever directory you changed.

### ⚠️ Before Committing: Always Verify

```bash
# Step 1: Check current directory
pwd

# Step 2: Check what will be committed
git status --short

# Step 3: Only then commit
git commit -m "message"
```

**Common Mistake:**
```bash
# ❌ WRONG - Committing to root when changes are in sub-repo
cd /
git add api/semantic.py  # Error: api/ is not in this repo!

# ✅ CORRECT - Go to the sub-repo first
cd memogarden-api
git add api/semantic.py
git commit -m "feat: add semantic API"
```

## Documentation Reference

The `/docs` folder contains detailed technical documentation:

- **[docs/repositories.md](docs/repositories.md)** - Complete repository structure, module breakdowns, and file listings
- **[docs/quickstart.md](docs/quickstart.md)** - Getting started guide
- **[docs/deployment.md](docs/deployment.md)** - Deployment guide (RPi, Docker, etc.)
- **[docs/configuration.md](docs/configuration.md)** - Configuration reference (environment variables, TOML)
- **[memogarden-api/tests/README.md](memogarden-api/tests/README.md)** - Testing philosophy and workflows

When working on database operations, API endpoints, or utilities, use the relevant **skills** for implementation patterns and code examples.

## Agent Subagents

The `.claude/agents/` directory contains specialized subagents that autonomously handle complex,
multi-step review tasks.

**Available Subagents:**
- **code-review** - Review code changes for implementation quality, style, patterns, and adherence to project guidelines (architecture.md patterns, coding standards, architectural constraints)
- **change-reviewer** - Review changes against project requirements and plans (PRD alignment, implementation plan accuracy, documentation completeness)

These subagents provide comprehensive review coverage with distinct focuses:
- **code-review** focuses on code quality and technical implementation
- **change-reviewer** focuses on project alignment and documentation

## Agent Skills

The `.claude/skills/` directory contains task-specific skills that provide detailed workflows
and guidance. Claude loads these skills automatically when relevant to your task.

**Available Skills:**
- **continue-implementation** - Session execution workflow: unpack session, execute, test, review, commit, improve (core plan retired; retained for per-project plans)
- **memogarden-development** - Development environment setup, architectural constraints, anti-patterns, shell command best practices, working with the implementation plan
- **memogarden-testing** - Testing philosophy, running tests, writing tests, debugging test failures
- **memogarden-api-endpoint** - Workflow for creating new API endpoints
- **memogarden-debugging** - Debugging workflows for database, API, and Core API issues
- **memogarden-schema** - SQLite schema modification workflow, data model reference
- **memogarden-refactor** - Code refactoring and test optimization (duplication analysis, test profiling, mock audit, test cleanup)
- **process-improvement** - Review session errors/mistakes and propose systemic improvements (tests, tools, skills)
- **change-commit** - Commit changes to git repository (use AFTER reviews)
- **feature-development** - Complete feature development workflow from issue to PR
- **bugfix** - Complete bugfix workflow from investigation to PR
- **gh** - GitHub operations workflow (issues, PRs, Actions)
- **claude-code-reference** - Reference for Claude Code features, hooks, slash commands, and capabilities
- **railway** - Railway CLI usage for cloud deployments, services, databases, and environment variables
- **test-dashboard** - Testing the Jupyter Telemetry Dashboard API (Flask)
- **jupyter-telemetry-assignments** - Assignment tagging workflow for Jupyter notebook telemetry

These skills allow AGENTS.md to focus on project context while providing detailed, task-specific
guidance on-demand. See [Claude Code Skills documentation](https://code.claude.com/docs/en/skills)
for how skills work.

## Essential Reading

Before working on any task, read these documents in order:

1. **[plan/memogarden_prd_v0_11_0.md](plan/memogarden_prd_v0_11_0.md)** - Platform Requirements Document
   - Complete platform architecture (Soil + Core)
   - All applications (Budget app, Project system)
   - Data model and entity definitions

2. **[plan/budget_prd.md](plan/budget_prd.md)** - Budget App Requirements (optional)
   - Budget app specific requirements
   - Financial transaction model
   - Read only when working on Budget app features

3. **[plan/memogarden-implementation-plan.md](plan/memogarden-implementation-plan.md)** - Implementation Plan — **RETIRED 2026-10-08**
   - Historical record of platform-core Sessions 1–14 (Feb 2026); not a source of current state
   - For current state: each repo's git history, and per-project plans (`mg-project-studio`, `mars`, `mush`)

## Technology Stack

### Backend (memogarden-system + memogarden-api)
- **Language**: Python 3.13
- **Framework**: Flask (synchronous)
- **Database**: SQLite (no ORM - raw SQL only)
- **Data Access**: Core API with centralized entity operations
- **Validation**: Pydantic schemas + `@validate_request` decorator
- **Testing**: pytest with pytest-flask
- **Package Manager**: Poetry with poetry-plugin-shell
- **Production Server**: gunicorn

### Frontend
- **mg-project-studio**: Svelte 5 + TypeScript + Vite + Tailwind; RFC-009 MemoGarden App for artifact-centric collaboration (SSE observe mode)
- **app-budget**: Flutter (Web + Android); transaction capture UI, development paused since Jan 2026
- **mars/flutter_apps/**: Flutter GUI clients for the MARS agent runtime

### Deployment
- **Local**: Development and testing
- **Production**: Raspberry Pi and/or Railway
- **Containerization**: Docker (when needed)
- **CD**: memogarden-devcd (webhook-based auto-deploy)

## Development Guidelines

**IMPORTANT**: When doing any development work on MemoGarden, ALWAYS use the **memogarden-development** skill. It contains critical architectural constraints, anti-patterns to avoid, and shell command best practices.

## Common Tasks

### Running Tests

**STANDARD WAY:** Use the standardized `run_tests.sh` script for each project. This ensures consistent behavior and provides grep-able output for automation.

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
./memogarden-api/run_tests.sh --format=md --tb=no -q 2>&1 | grep -A 5 "Test Summary"
```

**Why use run_tests.sh:**
- Ensures correct Poetry environment is used (doesn't rely on venv activation)
- Works from any directory (changes to project dir automatically)
- Provides grep-able output with test run ID and summary
- Last 6 lines always contain summary (use `tail -n 6` for quick status)
- Consistent behavior across all MemoGarden projects
- Centralized implementation via `scripts/test_entrypoint.sh` (easy to update globally)

**Format options for agents:**
- `--format=markdown` (or `--format=md`) - Markdown output, easier for parsing
- `--format=plaintext` - Plain text without borders
- `--format=textbox` (default) - Unicode bordered box

**Agent note:** If you need functionality not supported by `run_tests.sh`, alert a human to improve `scripts/test_entrypoint.sh` centrally rather than using ad-hoc bash commands.

**Legacy method (removed):**
The old `scripts/test.sh` script no longer exists; use `run_tests.sh` in each project.

See [`memogarden-api/tests/README.md`](memogarden-api/tests/README.md) for complete testing documentation.

For testing workflows, philosophy, and debugging test failures, use the **memogarden-testing** skill.

### Writing a New API Endpoint

For the complete workflow and patterns, use the **memogarden-api-endpoint** skill.

### Working with SQLite Schema

For schema modification workflow, data model reference, and conventions, use the **memogarden-schema** skill.

### Working with the Implementation Plan

For detailed guidance on working with the implementation plan, use the **memogarden-development** skill.

### Debugging

For comprehensive debugging workflows, use the **memogarden-debugging** skill.

### Code Refactoring

For code refactoring and test optimization, use the **memogarden-refactor** skill. This includes:
- Code duplication analysis and DRY refactoring
- Test profiling and optimization (target: <2.8s)
- Interface vs implementation testing
- Mock audit and removal
- Test cleanup and consolidation

### Process Improvement

For reviewing session errors/mistakes and proposing systemic improvements, use the **process-improvement** skill.

### Completing Work

For task completion workflow (git commits, status updates, implementation plan updates):
1. Use **code-review** subagent to review code quality and technical implementation
2. Use **change-reviewer** subagent to review changes against project plans and requirements
3. After reviews and user confirmation, use **change-commit** skill to commit changes

## Communication Guidelines

When working with the user:

1. **Read before asking** - Check PRD and implementation plan first
2. **Be specific** - Reference file paths and line numbers
3. **Explain trade-offs** - When multiple approaches exist
4. **Follow the plan** - Don't suggest jumping ahead unless critical
5. **Update docs** - Keep project docs and per-project plans current

## Project Values

1. **Simplicity** - Simple code > clever code
2. **Transparency** - Clear > concise
3. **Testability** - Test early and often
4. **Incrementalism** - Small steps > big leaps
5. **Flexibility** - Learn and adjust

## Getting Help

If stuck or unsure:

1. Re-read the PRD for context
2. Check the workspace README and the relevant project's own plan/docs for current scope
3. Look at existing code patterns
4. Ask the user with specific questions
5. Propose options with trade-offs

---

**Last Updated**: 2026-10-08
**For**: AI agents working on MemoGarden
**Maintained by**: Project contributors
