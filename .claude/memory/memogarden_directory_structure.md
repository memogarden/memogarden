---
name: MemoGarden Directory Structure
description: MemoGarden multi-repo project structure with separate project directories and root coordination repo
type: project
---

# MemoGarden Directory Structure

## Overview

MemoGarden uses a **multi-repo structure** where each major component has its own git repository, while coordination and planning happens in the root `memogarden` repository.

## Repository Architecture

```
/home/kureshii/memogarden/                    # Root coordination repo (NO REMOTE)
├── .claude/                                # Claude Code configuration
│   ├── skills/                            # Agent skills
│   ├── agents/                            # Agent subagents
│   └── memory/                            # Persistent memory (this file!)
├── collection-claude-code-source-code/    # Untracked (source code archive)
├── docs/                                  # Project documentation
├── mars/                                  # MARS agent project (separate repo)
├── memogarden-api/                        # MemoGarden API project (separate repo)
├── memogarden-system/                     # MemoGarden Core library (separate repo)
├── memogarden-client/                     # MemoGarden client (separate repo)
├── app-budget/                            # Budget Flutter app (separate repo)
├── memogarden-devcd/                       # CD/automation (separate repo)
├── memogarden.github.io/                  # Documentation site (separate repo)
└── hacm/                                 # Headless Agent Credential Manager (separate repo)
```

## Repository Details

### Root Repository: `memogarden/` (NO REMOTE)
- **Purpose**: Coordination, planning, documentation, agent configuration
- **Contents**: 
  - `.claude/` - Claude Code configuration (skills, agents, memory)
  - `docs/` - Project planning documentation
  - CLAUDE.md - Project context for AI agents
  - README.md - Project overview
- **Git**: Has local git but NO remote (pure coordination repo)
- **Branch**: `main`

### Project Repositories (each with own git history)

#### MARS Agent: `mars/`
- **Purpose**: Multi-agent Runtime System for agent execution
- **Remote**: https://github.com/yourusername/mars (or similar)
- **Contents**: 
  - `src/mars/` - MARS agent source code
  - `tests/` - Test suite including GUI integration tests
  - `pyproject.toml` - Python dependencies
- **Git**: Full repository with remote
- **Recent work**: GUI integration test fixes, test isolation improvements

#### MemoGarden API: `memogarden-api/`
- **Purpose**: Flask REST + Semantic API for MemoGarden Core
- **Remote**: GitHub repository
- **Contents**:
  - `api/` - Flask API endpoints
  - `tests/` - API tests
- **Git**: Full repository with remote

#### MemoGarden System: `memogarden-system/`
- **Purpose**: Core library (Soil + Core layers)
- **Remote**: GitHub repository
- **Contents**:
  - `system/` - Database operations, utilities, exceptions
- **Git**: Full repository with remote

#### MemoGarden Client: `memogarden-client/`
- **Purpose**: Client library for MemoGarden
- **Remote**: GitHub repository (if exists)
- **Git**: Full repository with remote

#### Budget App: `app-budget/`
- **Purpose**: Flutter budget app (Web + Android)
- **Remote**: Future development
- **Git**: Full repository with remote

#### Development CD: `memogarden-devcd/`
- **Purpose**: Continuous deployment automation
- **Remote**: GitHub repository
- **Git**: Full repository with remote

#### Documentation Site: `memogarden.github.io/`
- **Purpose**: Public documentation website
- **Remote**: GitHub repository
- **Git**: Full repository with remote

#### HACM: `hacm/`
- **Purpose**: Headless Agent Credential Manager
- **Remote**: GitHub repository
- **Git**: Full repository with remote

## Working with Multiple Repositories

### ⚠️ CRITICAL: Always Verify Repository Before Committing

```bash
# Step 1: Check current directory
pwd

# Step 2: Check what will be committed
git status --short

# Step 3: Only then commit
git commit -m "message"
```

### Common Mistake: Cross-Repository Commits

❌ **WRONG** - Attempting to commit to root when changes are in sub-repo:
```bash
cd /home/kureshii/memogarden
git add mars/tests/test_gui.py  # Error: mars/ is not in this repo!
```

✅ **CORRECT** - Go to the sub-repo first:
```bash
cd /home/kureshii/memogarden/mars
git add tests/test_gui.py
git commit -m "feat: add GUI test"
```

### Repository Boundaries

- **Root Repository** (`/`): Contains planning, documentation, agent config only
- **API Repository** (`memogarden-api/`): Contains Flask app, API endpoints, tests
- **System Repository** (`memogarden-system/`): Contains database operations, utilities
- **MARS Repository** (`mars/`): Contains agent runtime, GUI, tests

Each repository has **independent git history** and must be committed to separately.

## When to Update This Memory

**Update this memory when:**
- Adding a new project directory
- Removing a project directory
- Restructuring the repository layout
- Changing repository purposes

**Last Updated**: 2026-05-30
**Projects Active**: 8 separate repositories
