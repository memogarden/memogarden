# Project Studio Specification

**Version:** 0.4.3
**Status:** Draft
**Last Updated:** 2026-11-28
**Supersedes:** MemoGarden Project System PRD v0.3.0

---

## 1. Overview

### 1.1 Purpose

This document specifies **Project Studio**, a JCE studio for artifact-centric collaboration on structured work products. It enables dyads (typically operator + agent) to co-develop documents, code, designs, and other artifacts through conversation-driven iteration.

### 1.2 Position in JCE Stack

```
┌─────────────────────────────────────────────────────────────────┐
│  Project Studio  ◄── This document                              │
├─────────────────────────────────────────────────────────────────┤
│  Utilities: Artifact Editor, Conversation Groups, Timeline      │
│  Browser, Shell                                                 │
├─────────────────────────────────────────────────────────────────┤
│  MemoGarden Substrate: Soil, Core, Relations                    │
│  (PRD v0.6.0, RFC-001, RFC-002, RFC-003)                        │
└─────────────────────────────────────────────────────────────────┘
```

Project Studio **composes** utilities; it does not redefine them. This specification covers:
- How utilities combine for artifact-centric workflows
- Project-specific data structures (Project, Stack, Frame)
- Workflow patterns and coordination
- Tool operations specific to artifact work

### 1.3 Relationship to Other Documents

| Document | Relationship |
|----------|--------------|
| JCE Whitepaper v1.0 | Parent architecture. Project Studio is one of several studios. |
| PRD v0.6.0 | Substrate specification. All Items, Relations, Artifacts defined there. |
| RFC-001 v4 | Security model. Tool execution, API keys, audit trails. |
| RFC-002 v5 | Time horizon. Relation decay, fossilization. |
| RFC-003 v4 | Context capture. View-stream, ContextFrame, delta.context. |

---

## 2. Core Principles

1. **Artifact-First**: Artifacts are primary work products; conversations are context for artifact evolution
2. **Git-Inspired Versioning**: Familiar commit semantics (delta, parent, commit hash)
3. **Stack-Based Exploration**: Branch freely, fold learnings back to main path
4. **Fragment Granularity**: Messages decompose into semantic units for precise reference
5. **Inline References**: Links parsed from natural language, rendered as interactive elements
6. **Participant Symmetry**: Operator and agent use same tools, leave same traces
7. **Explicit Causation**: System relations (triggers) enable audit trails and retrace

---

## 3. Composed Utilities

Project Studio combines these JCE utilities:

| Utility | Role in Project Studio |
|---------|------------------------|
| **Artifact Editor** | Primary work surface; create, edit, version artifacts |
| **Conversation Groups** | Discussion threads driving artifact changes |
| **Timeline Browser** | Navigate project history; inspect items, relations |
| **Shell** | Command-line access to all operations |

### 3.1 Artifact Editor (Primary Surface)

Within Project Studio, Artifact Editor provides:

- Line-numbered content display
- Delta-based versioning with commit hashes
- Side-by-side diff against any commit
- Branch/merge for parallel editing
- Context capture on edit (ContextFrame snapshot in `delta.context`)

**Project Studio additions:**
- Propose-accept workflow (agent proposes, operator accepts/rejects)
- Linked conversations (see which discussion drove each change)
- Export to standard formats (Markdown, PDF, code files, DOCX)

### 3.2 Conversation Groups (Discussion Threads)

Within Project Studio, Conversation Groups provide:

- ConversationLogs scoped to project
- Messages containing Fragments (semantic units)
- Fold lifecycle (summarize branches)
- System relations linking messages to artifacts they reference

**Project Studio additions:**
- Artifact-anchored threads (discussion about specific lines)
- Decision capture (fold summary records what was decided)

### 3.3 Timeline Browser (History Navigation)

Within Project Studio, Timeline Browser provides:

- Filter by artifact, participant, time range
- Trace trigger chains (what caused what)
- Object viewer for any Item, Artifact, Relation
- Fidelity state inspection (full, summary, stub, tombstone)

**Project Studio additions:**
- Artifact-centric view (all deltas for one artifact)
- Decision timeline (folded branches as milestones)

---

## 4. Project-Specific Data Structures

These structures exist in addition to substrate entities (defined in PRD v0.6.0).

### 4.1 Project

**Purpose:** Top-level container for a body of work

**Schema:**
```python
@dataclass
class Project:
    uuid: str                           # core_ prefix
    label: str                          # Human-readable name
    stack: list[str]                    # ConversationLog UUIDs in creation order
    artifact_uuids: list[str]           # Artifacts belonging to this project
    frames: dict[str, Frame]            # participant_id → Frame
    created_at: datetime
    updated_at: datetime
```

**Properties:**
- Lives in Core (mutable)
- Contains references to artifacts and conversations, not copies
- Multiple projects can reference same artifact (rare but possible)

### 4.2 Stack

**Purpose:** Ordered record of all conversation branches in project

**Schema:**
```python
Stack = list[str]  # ConversationLog UUIDs in creation order
```

**Properties:**
- Insertion order preserved (first branch created is first in list)
- No duplicates
- Tree structure derived from `ConversationLog.parent_uuid`, not stack order
- Includes both active and folded branches

### 4.3 Frame

**Purpose:** Tracks a participant's current working position in a branch tree

**Schema:**
```python
@dataclass
class Frame:
    branch_uuid: str        # Which ConversationLog this participant is working in
    head: str | None        # Item UUID of latest item in this frame's view
```

**Properties:**
- One Frame per participant per project
- Independent positioning: operator and agent can work in different branches
- `head` tracks latest item seen/created by this participant
- Persists across sessions (participant resumes where they left off)

**Distinction from ContextFrame:**

| Frame | ContextFrame |
|-------|--------------|
| Branch position | Attention state |
| "Where am I in the tree?" | "What am I looking at?" |
| Project-specific | Cross-project |
| One per participant per project | One per participant globally |
| Defined here | Defined in RFC-003 |

Both exist; they serve different purposes.

---

## 5. Substrate Entities (Reference)

These entities are defined in PRD v0.6.0. This section summarizes their use in Project Studio.

### 5.1 Item Hierarchy

```
Item (base: uuid, _type, realized_at, canonical_at, integrity_hash, fidelity)
├── Note (description, summary?, title?)
│   └── Message (sender, recipient?, subject?, fragments)
├── ToolCall (tool, operation, params, result?, caller, context?)
├── ArtifactCreated (artifact_uuid, label)
├── ArtifactDelta (artifact_uuid, commit, parent?, ops, references, context)
└── SystemEvent (event_type, payload?)
```

**UUID Prefixes:**
- Items in Soil: `soil_` prefix
- Entities in Core: `core_` prefix

### 5.2 Artifact

**Schema (from PRD v0.6.0):**
```python
@dataclass
class Artifact:
    uuid: str                       # core_ prefix
    label: str                      # User-facing name
    content: str                    # Current content (line-numbered)
    deltas: list[str]               # UUIDs of ArtifactDelta Items in Soil
    snapshots: dict[str, str]       # commit → content
    label_history: list[str]        # Past labels
```

### 5.3 ArtifactDelta

**Schema (from PRD v0.6.0):**
```python
@dataclass
class ArtifactDelta(Item):
    artifact_uuid: str              # Which Artifact this changes
    commit: str                     # Hash of resulting state
    parent: str | list[str] | None  # Parent commit(s); None for first
    ops: str                        # "+15:^a7f -23 ~18:^b2e→^c3d"
    references: list[str]           # Fragment IDs that informed this change
    context: list[str]              # ContextFrame snapshot at mutation time
```

### 5.4 ConversationLog

**Schema (from PRD v0.6.0):**
```python
@dataclass
class ConversationLog:
    uuid: str                           # core_ prefix
    parent_uuid: str | None             # Branch parent (null for root)
    items: list[str]                    # Item UUIDs in chronological order
    summary: Summary | None             # Present if folded
    collapsed: bool                      # True if branch has been folded

@dataclass
class Summary:
    timestamp: datetime
    author: Literal["operator", "agent", "system"]
    content: str
    fragment_ids: list[str]
```

### 5.5 Message and Fragment

**Schema (from PRD v0.6.0):**
```python
@dataclass
class Message(Note):
    sender: str                     # Participant identifier
    recipient: str | None
    subject: str | None
    fragments: list[Fragment]

@dataclass
class Fragment:
    id: str                         # ^a7f (content hash)
    content: str                    # May contain inline references
```

### 5.6 System Relations

**Relevant kinds for Project Studio:**

| Kind | Use in Project Studio |
|------|----------------------|
| `triggers` | Message → ArtifactDelta causation |
| `cites` | Artifact A references Artifact B |
| `derives_from` | New artifact synthesized from existing |
| `contains` | Project → Artifacts (structural) |

---

## 6. Fragment System

### 6.1 Purpose

Fragments are atomic semantic units within Messages. They enable:
- Precise reference to specific ideas
- Tracking which ideas informed which changes
- Granular citation in artifacts

### 6.2 Fragment Generation

Messages are decomposed into Fragments server-side (or by agent).

```python
def generate_fragment_id(content: str) -> str:
    """Generate 3-character base36 hash from content."""
    hash_bytes = hashlib.sha256(content.encode()).digest()[:2]
    hash_int = int.from_bytes(hash_bytes, 'big')

    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    result = ""
    while hash_int > 0:
        result = chars[hash_int % 36] + result
        hash_int //= 36

    return f"^{result.zfill(3)}"
```

**Collision handling:** Disambiguate via `(message_uuid, position)`.

### 6.3 Reference Syntax

**Internal representation:**
Fragment IDs are exactly `^` followed by 3 lowercase alphanumeric characters.

**Rendered display:**
References are opaque to users. The system parses, resolves, and renders them as interactive elements.

### 6.4 Reference Types

| Syntax | Type | Resolves To |
|--------|------|-------------|
| `^a7f` | Fragment | Specific idea in a message |
| `label:line` | Artifact line | Line in current artifact state |
| `label:line@commit` | Artifact line at commit | Line in historical state |
| `@uuid` | Item | Any Item by UUID |
| `[text](uuid)` | Log | ConversationLog |

### 6.5 Reference Parsing

```python
def parse_references(content: str) -> list[dict]:
    refs = []

    # Fragment refs: ^<hash> (exactly 3 chars after caret)
    for match in re.finditer(r'\^[a-z0-9]{3}', content):
        refs.append({'type': 'fragment', 'id': match.group(), 'span': match.span()})

    # Artifact line refs: <label>:<line>[@<commit>]
    for match in re.finditer(r'([\w_]+):(\d+)(?:@([a-f0-9]+))?', content):
        refs.append({
            'type': 'artifact_line',
            'label': match.group(1),
            'line': int(match.group(2)),
            'commit': match.group(3),
            'span': match.span()
        })

    # Log refs: [text](uuid)
    for match in re.finditer(r'\[.*?\]\(((?:soil|core)_[\w-]+)\)', content):
        refs.append({'type': 'log', 'uuid': match.group(1), 'span': match.span()})

    # Item refs: @<uuid>
    for match in re.finditer(r'@((?:soil|core)_[\w-]+)', content):
        refs.append({'type': 'item', 'uuid': match.group(1), 'span': match.span()})

    return refs
```

---

## 7. Tool Operations

### 7.1 Shared Tool Model

Both participants use the same tools. This table shows equivalence:

| Operation | Agent Invocation | Operator Equivalent |
|-----------|------------------|---------------------|
| Find content | `semantic_search(artifact, query)` | Search box |
| Focus attention | `scroll_to(artifact, start, end)` | Scroll, tap line |
| Expand context | `scroll_up(n)` / `scroll_down(n)` | Scroll gesture |
| Take notes | `note(content)` | Annotation |
| Select for reference | `select_lines(artifact, start, end)` | Tap/drag select |
| Propose change | `propose_edit(artifact, range, content, reasoning)` | Stage edit |
| Create artifact | `create_artifact(label, content)` | New document |
| Commit change | `commit_delta(artifact, ops)` | Save |

All tool invocations are recorded as ToolCall Items in Soil.

### 7.2 Artifact Operations

**Create:**
```python
create_artifact(label: str, initial_content: str = "") -> Artifact
```
Creates:
- Artifact in Core
- ArtifactCreated Item in Soil
- System relation: `contains(project, artifact)`

**Edit:**
```python
commit_delta(
    artifact_uuid: str,
    ops: str,                    # "+5:^a7f -10 ~3:^b2e→^c3d"
    references: list[str],       # Fragment IDs that informed change
    based_on_commit: str         # For optimistic locking
) -> ArtifactDelta
```
Creates:
- ArtifactDelta Item in Soil (with `context` field from current ContextFrame)
- Updates Artifact.content in Core
- System relation: `triggers(source_item, delta)` if applicable

Commit processing (fragment decomposition, extent computation, extent persistence) is
owned by the **Artifact Handler** (§15) — the main service validates, gates, and
persists, but does not implement artifact handling itself.

**Delta Operations Syntax:**
```
+15:^a7f              Add fragment a7f at line 15
-23                   Remove line 23
~18:^b2e→^c3d         Replace line 18: fragment b2e with c3d
>12@30                Move line 12 to position 30
```

**Read:**
```python
read_artifact(artifact_uuid: str, start: int = None, end: int = None) -> str
semantic_search(artifact_uuid: str, query: str) -> list[Match]
get_artifact_at_commit(artifact_uuid: str, commit: str) -> str
diff_commits(artifact_uuid: str, commit_a: str, commit_b: str) -> Diff
```

### 7.3 Conversation Operations

**Send message:**
```python
send_message(
    log_uuid: str,
    content: str,
    sender: str
) -> Message
```
Creates:
- Message Item in Soil (with auto-generated Fragments)
- Appends to ConversationLog.items
- System relations from any references in content

**Fold:**
```python
fold(
    log_uuid: str,
    summary_content: str,
    author: Literal["operator", "agent", "system"]
) -> ConversationLog
```
Folds a conversation branch by adding a summary.

Per RFC-005: `fold` is a single-word verb applicable to any entity/fact.

Creates:
- Summary object attached to ConversationLog
- Marks branch as folded (collapsed=true)
- Branch remains visible and can continue (append messages after fold point)

**Note on Branching:** Branch creation happens implicitly via RFC-003 ContextFrame inheritance. When a subagent is created with its own ContextFrame that inherits from a parent, this implicitly creates a conversation branch. No explicit `branch` verb is needed.

---

## 8. Coordination Patterns

Project Studio uses JCE coordination primitives (defined in JCE Whitepaper).

### 8.1 Handover

Transfer active work on an artifact to partner.

```
Operator editing requirements.md
  → Handover("add error handling for edge cases in section 3")
  → Agent's ContextFrame inherits Operator's
  → Agent's Frame points to same branch
  → Agent works, Operator can observe or switch tasks
  → Agent yields with summary
  → Operator reviews delta(s), accepts or continues
```

**Implementation:**
- Handover creates SystemEvent Item recording transfer
- Agent receives trigger event with context
- Completion triggers Yield

### 8.2 Fork (Parallel Exploration)

Both participants explore options in parallel.

```
Discussion: "Should we use REST or GraphQL?"
  → Fork: Agent explores REST in branch_rest
          Operator explores GraphQL in branch_graphql
  → Each participant's Frame points to their branch
  → Work proceeds independently
  → Reconcile: Compare findings
  → Collapse losing branch, merge winner, or keep both
```

**Implementation:**
- Two participants create branches via ContextFrame inheritance
- Frames diverge (each has different branch_uuid)
- Reconcile is manual (message in parent branch) or triggered

### 8.3 Observe (Follow Mode)

Operator watches agent work in real-time.

```
Agent performing multi-step retrieval and synthesis
  → Operator enables Follow mode
  → Operator's view tracks Agent's ContextFrame
  → Operator sees: searches, scroll positions, proposed edits
  → Operator can: interrupt, redirect, annotate, or let continue
  → Follow mode ends on Operator action or explicit exit
```

**Observability modes:**

| Mode | Description |
|------|-------------|
| Independent | Default. Each participant has own view. |
| Follow | Operator's view tracks Agent's attention live. |
| Retrace | Replay Agent's activity via trigger chain traversal. |

### 8.4 Summon

Pull partner's attention to current focus.

```
Operator finds relevant passage in research_notes.md
  → Summon("this contradicts our assumption in goals_doc:15")
  → Agent's Frame updated with Operator's focus
  → Agent acknowledges and responds
```

### 8.5 Yield

Signal completion and return control.

```
Agent finishes implementing error handling
  → Yield(summary="Added try/catch blocks for network errors in section 3")
  → Summary added to conversation
  → Control returns to Operator
  → Operator reviews and accepts or requests changes
```

---

## 9. Workflow Patterns

### 9.1 Draft → Review → Commit

Standard artifact evolution cycle.

```
1. Operator or Agent drafts content (propose_edit)
2. Partner reviews (read_artifact, diff)
3. Discussion if needed (send_message)
4. Commit (commit_delta) or revise
5. Repeat
```

### 9.2 Branch for Exploration

Speculative work without polluting main line.

```
1. Identify decision point
2. Create branch (via ContextFrame inheritance)
3. Explore in branch (may create/edit artifacts)
4. Conclude: fold with summary, or abandon
5. Summary captures learnings; main line continues
```

### 9.3 Multi-Artifact Change

Single decision affecting multiple artifacts.

```
Message: "Switch from REST to GraphQL"
  → triggers → ArtifactDelta (api_spec.md)
  → triggers → ArtifactDelta (frontend_guide.md)
  → triggers → ArtifactDelta (backend_notes.md)

All deltas share:
- Same triggering message
- Same context snapshot
- References to decision fragment
```

### 9.4 Parallel Editing with Merge

Both participants edit same artifact in different branches.

```
1. Fork from common point
2. Each edits artifact in their branch
3. Merge:
   - If non-overlapping: auto-merge
   - If conflicting: conversation to resolve
4. Merged delta has multiple parents
```

---

## 10. Storage Layout

### 10.1 Core Structure

```
/projects/
  core_project_abc/
    project.json              # Project metadata, stack, frames

/artifacts/
  core_artifact_xyz/
    metadata.json             # uuid, label, label_history
    content.txt               # Current content
    snapshots/
      a1b2c3d.txt             # Historical snapshots by commit

/logs/
  core_log_001.json           # ConversationLog
  core_log_002.json
```

### 10.2 Soil Tables

```sql
-- Items (all types)
CREATE TABLE item (
    uuid TEXT PRIMARY KEY,              -- soil_ prefix
    _type TEXT NOT NULL,
    realized_at TEXT NOT NULL,
    canonical_at TEXT NOT NULL,
    integrity_hash TEXT,
    fidelity TEXT NOT NULL DEFAULT 'full',
    fossilized_at INTEGER,
    data JSON NOT NULL
);

-- System relations
CREATE TABLE system_relation (
    uuid TEXT PRIMARY KEY,              -- soil_ prefix
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    source_type TEXT NOT NULL,
    target TEXT NOT NULL,
    target_type TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    evidence JSON,
    metadata JSON,
    UNIQUE(kind, source, target)
);
```

---

## 11. Integration with Substrate Features

### 11.1 Time Horizon (RFC-002)

User relations in Project Studio follow standard time horizon decay.

- Explicit links between artifacts: time horizon tracks engagement
- Frequently co-accessed artifacts accumulate longer horizons
- Fossilization compresses old, unaccessed artifacts

**Project-specific consideration:** Project membership (`contains` relation) is a system relation—it doesn't decay. The artifacts within may fossilize, but the structural fact "this artifact belongs to this project" persists.

### 11.2 Context Capture (RFC-003)

Every ArtifactDelta captures ContextFrame at mutation time.

```python
delta.context = ["core_artifact_xyz", "core_log_001", "core_artifact_abc"]
```

This enables:
- "What was I looking at when I made this change?"
- Co-access analysis across project history
- Reconstruction of decision context

### 11.3 Security (RFC-001)

All tool operations:
- Require valid API key with appropriate scopes
- Are logged as ToolCall Items in Soil
- Respect agent budget policies

**Project-specific scopes:**
- `project:read` — view project, artifacts, conversations
- `project:write` — edit artifacts, send messages
- `project:admin` — create/delete projects, manage access

---

## 12. Export and Import

### 12.1 Export Formats

| Artifact Type | Export Options |
|---------------|----------------|
| Text/prose | Markdown, PDF, DOCX, TXT |
| Code | Source files with appropriate extensions |
| Structured data | JSON, YAML |
| Mixed | Markdown with embedded code blocks |

**Export includes:**
- Current content
- Optionally: full delta history as appendix
- Optionally: linked conversation excerpts

### 12.2 Import

External files become artifacts:

```python
import_file(
    project_uuid: str,
    file_path: str,
    label: str
) -> Artifact
```
Creates:
- Artifact with file content
- ArtifactCreated Item
- System relation: `derives_from(artifact, external_source)` if source tracked

---

## 13. Open Questions

### 13.1 Resolved (from PRD v0.3.0)

| Question | Resolution |
|----------|------------|
| Frame persistence | Persists across sessions (participant resumes where they left off) |
| Fossilization storage | Per RFC-002: fidelity states (full → summary → stub → tombstone) |

### 13.2 Still Open

1. **Scratchpad semantics:** Ephemeral working space before committing to artifact. Options:
   - Ephemeral artifact (no deltas until "commit to permanent")
   - Special ConversationLog type
   - Feature within Artifact Editor (staging area)

2. **Parallel branch display:** Side-by-side vs tabs vs other UI patterns

3. **Fold summary format:**
   - Operator-authored only?
   - Agent-generated with operator approval?
   - System-generated from message analysis?

4. **Reference autocomplete:** UX for creating references while typing

5. **Cross-project references:** Can artifact in Project A reference artifact in Project B?

6. **Merge conflict UX:** When parallel edits conflict, what's the resolution flow?

---

## 14. Future Work

### 14.1 Code-Specific Features

For code artifacts:
- Syntax highlighting
- Lint/test integration
- Git interop (export branch as git commits)

### 14.2 Template Artifacts

Pre-structured artifacts for common patterns:
- PRD template
- RFC template
- Meeting notes template

### 14.3 Artifact Schemas

Formal schemas for structured artifacts:
- Validate content against schema
- Schema-aware editing
- Schema migration on updates

### 14.4 Voice Input

Mobile-first voice input for:
- Message dictation → Fragment generation
- Voice annotations on artifacts

---

## 15. Artifact Handler

**Added v0.4.2 (operator ruling, 2026-11-28); §15.4–15.6 corrected v0.4.3 (extents
are canonical service state, not per-app derived metadata — multi-app referencing
requires a single resolver).** The main MemoGarden service (memogarden-system /
memogarden-api) does **not** take on responsibility for *handling* artifacts.
Artifact handling — fragment decomposition, extent determination, extent refresh on
edit — is owned by a dedicated **Artifact Handler** component; the main service
persists and serves the results as canonical state. This section specifies the
responsibility boundary and the storage design; implementation mechanics live with
the handler's own repository when built.

### 15.1 Boundary ruling

| Concern | Owner |
|---------|-------|
| Artifact as Core entity (belief state, hash chain, §4.5 gates) | Main service (unchanged) |
| `/mg` verb surface: validation, optimistic locking, Soil delta items, SSE | Main service (unchanged) |
| Fragment decomposition (§6.2), fragment ID generation | **Artifact Handler** |
| **Fragment extents** — determination, storage, refresh on edit | **Artifact Handler** |
| Persistence plumbing for handler output (side-car write, catch-up) | Thin bridge module in the main service process (plumbing only — no handling logic) |

The bridge must stay thin: if fragment/extent logic starts accumulating in the main
service, the ruling is being violated in substance.

### 15.2 Fragment extents

§6.2 treats a fragment as a *point* reference (insert-at-line). Fragments are actually
*spans*: an **extent** locates the fragment in the current artifact state.

- Extent = `(fragment_id, start_line, end_line)` — line-inclusive, resolved against a
  specific artifact content version. Char-level extents are a future refinement; v1 is
  line-granular.
- Fragment IDs remain content-hash based (§6.2 `generate_fragment_id`), which gives
  **id stability across edits**: a fragment whose text is unchanged keeps its ID; only
  its position (extent) moves.
- This generalizes the §6.4 reference type `label:line` → fragment references resolve
  to spans via the extent set.

### 15.3 Handler shape

The handler follows the memogarden-docling operational template: a **content-agnostic
worker** in its own repository, reached over a local unix socket (socket-activated user
units, idle self-exit), with its own pinned runtime. It holds **no MemoGarden
credentials and no Core/DB access** — text in, spans out:

```
input:  { artifact_uuid, base_content, new_content, fragment_registry? }
output: { fragments: [ { id, content, start_line, end_line } ],
          removed: [ id ], extents_version }
```

### 15.4 Extent storage — canonical service state (corrected v0.4.3)

v0.4.2 classified extents as derived side-car (the `entity_fts` posture). **That
classification was wrong for this data.** The FTS analogy fits *per-consumer indexes*
— search hits that only one service reads. Fragments are *communication*: Project
Studio, ArtiLab, generic viewers, and agent editors must all reference the **same**
fragment set, or shared annotations and cross-app communication become impossible.
Per-app derived extents would create exactly that fragmentation. Therefore:

- **Fragment identity needs no sync at all**: IDs are content hashes (§6.2) —
  identical content anywhere yields the identical ID. Identity is self-converging;
  there is no registry to distribute.
- **Extents are canonical main-service state**: a first-class table in the canonical
  schema (its own `artifact_fragment_extent` table — the Artifact *entity* schema
  stays untouched, honoring the original constraint; the dependency runs one way:
  extents reference artifacts, never the reverse). Rows are written transactionally
  in the commit boundary and are **immutable per commit**: the extent set for a
  given commit is a pure function of that commit's content. The current set is the
  head pointer; history is per-commit sets.
- The `entity_fts` posture (runtime-ensured, outside canonical schema, rebuildable)
  remains reserved for genuine indexes that consumers reach through one service. It
  does not fit addressable, cross-app state.

Rejected alternatives (unchanged from v0.4.2, plus the retraction): a reserved key
in Artifact `data` (schema dependency in substance); a Fragment *entity* type
(entity explosion, consistency burden); a handler-owned store (split-brain against
Core's single-writer discipline); and *per-app derived extents* (this section's
correction — unsyncable across apps).

### 15.5 Multi-app coherence — the single-resolver rule

Nothing needs syncing, because **no app ever holds authoritative extents**:

1. **One resolver**: reference resolution (`^a7f` → span + content) is a
   main-service read surface backed by the canonical table. Apps ask; they never
   compute. Two apps citing `^a7f` on the same artifact resolve to the same span
   and the same content because the ID is the content hash and the resolver is
   singular.
2. **Cache by commit, safely**: apps may cache extent sets keyed by
   `(artifact_uuid, commit)`. This cannot diverge: per-commit sets are immutable,
   so a stale cache is *detectable* (the head commit moved) and never *conflicting*.
3. **Shared annotations cite, not store**: an annotation from any app — Project
   Studio message, ArtiLab comment, agent editor — carries
   `(artifact_uuid, fragment_id)` (optionally pinned `@commit` per §6.4). The
   service resolves it identically for every other app, present or future. Shared
   communication works precisely because the fragment set is singular and
   server-side.
4. Client-side decomposition (e.g. a live preview while editing) is permitted only
   as cache-fill and must be validated against the commit it claims before any
   reference is *published*; unpublished preview state is ephemeral by construction.

### 15.6 Edit flow — extent refresh (revised)

1. Client submits a commit (ops or full content) → main service validates `based_on`
   hash + §4.5 gates, creates the ArtifactDelta, updates `Artifact.content` — the
   artifact's belief lands **first**.
2. Bridge invokes the handler with (base content, new content) → fresh extent set
   (re-decomposition is exact and cheap at ≤400k chars; unchanged fragments keep
   their IDs, so the extent diff is meaningful).
3. **Handler reachable**: extent rows are written in the same transaction boundary —
   content, delta, and extents land as one atomic step.
4. **Handler down**: the commit still lands (belief capture outranks reference
   infrastructure), and the new commit is marked `extents_pending`. Reference
   resolution for that commit degrades *explicitly* — content reads are unaffected;
   the resolver answers "pending", never silently wrong. Catch-up is
   change_log-driven (system#12) and safe because handler output is deterministic —
   a pure function of content — so late computation converges to the same set
   whenever it runs. Concurrency is unchanged: all Core writes remain in the main
   service process; the handler never touches the database.

Staleness here is bounded, visible, and self-healing toward a unique answer — the
precondition that makes pending-degradation acceptable for canonical data.

### 15.7 Client impact

Project Studio and ArtiLab (and any future artifact client) stop owning fragment
registries entirely: they render and resolve references via the service's read
surfaces, and cache per §15.5. The §6.2 client-side fragment protocol becomes one
supported input shape (ops with client-supplied fragments for agent-driven edits),
not a requirement for every client — and never the basis for cross-app references.

### 15.8 Migration and non-goals

- Extents now need a proper (tiny) canonical-schema migration — trivially empty at
  zero production deltas; legacy point-ops remain valid (a point insert is a 1-line
  extent).
- Non-goals for v1: char-level extents, cross-artifact fragments, semantic analysis
  inside the handler (the stage-3 statement pipeline may later ride the same handler,
  but that is a separate decision).

---

## 16. Revision History

| Version | Date | Changes |
|---------|------|---------|
| 0.1.0 | 2025-12-26 | Initial draft (as "MemoGarden Project System PRD") |
| 0.2.0 | 2025-12-27 | Added ToolCall, Frame, stack semantics, JCS shared tool model |
| 0.3.0 | 2025-12-29 | Storage split (Soil/Core), Item hierarchy, Trigger relations |
| 0.4.0 | 2026-02-12 | **Revised conversation operations:** Replace `collapse_conversation`/`reopen_conversation` with `fold`; clarified implicit branching via RFC-003 ContextFrame inheritance. Aligned with RFC-005 single-word verb convention. |
| 0.4.2 | 2026-11-28 | **Added §15 Artifact Handler** (operator ruling): main service does not handle artifacts — dedicated handler owns fragment decomposition, extents, extent storage + refresh; extents stored as derived side-car (entity_fts precedent) with delta-trail traceability, Artifact schema untouched; §7.2 cross-ref. (0.4.1 edits carried unversioned.) |
| 0.4.3 | 2026-11-28 | **§15.4–15.6 corrected (operator review): extents are CANONICAL main-service state, not per-app derived metadata** — multi-app referencing (Project Studio / ArtiLab / viewers / agent editors) needs one resolver or shared annotations fragment; new first-class `artifact_fragment_extent` canonical table (Artifact entity schema still untouched), immutable per commit (pure function of content), single-resolver rule + cache-by-commit (§15.5), transactional write with explicit `extents_pending` degradation when the handler is down (§15.6) |

---

## 17. References

- JCE Whitepaper v1.0 — Parent architecture
- MemoGarden PRD v0.6.0 — Substrate specification
- RFC-001 v4 — Security and operations
- RFC-002 v5 — Time horizon and fossilization
- RFC-003 v4 — Context capture mechanism
- RFC-005 v7 — Semantic API verb conventions

---

**END OF DOCUMENT**
