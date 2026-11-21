# RFC-008: Transaction Semantics & Consistency Model

**Version:** 1.3  
**Status:** Draft  
**Author:** JS  
**Date:** 2026-02-08

---

## 1. The Constraint

**SQLite has no distributed transaction protocol.** MemoGarden uses two SQLite databases:
- **Soil:** Immutable facts (Items, SystemRelations)
- **Core:** Mutable state (Entities, UserRelations, ContextFrames)

When an operation must update both databases atomically—like `update_entity()` creating both an EntityDelta in Soil and updating Entity state in Core—there is no two-phase commit (2PC) coordinator to guarantee atomicity.

**The vulnerability window:** Between Soil commit and Core commit, power loss creates inconsistency. For a personal system with 100 writes/day:
- Window duration: ~100ms per transaction
- Daily exposure: 10 seconds
- Annual exposure: ~36 seconds
- **Failure rate: ~0.01% per year (~1 failure per 100 years)**

This is the fundamental constraint that drives all transaction design decisions.

---

## 2. The Design Choice

**Accept the failure window. Design for detection and repair rather than prevention.**

**Rationale:**
- **Survivability:** System always starts, never deadlocks, never requires distributed transaction logs
- **Simplicity:** No write-ahead log, no 2PC coordinator, no complex recovery protocols  
- **Pragmatism:** 0.01%/year failure rate acceptable for personal system with operator-assisted recovery
- **Hardware reality:** Raspberry Pi can survive power loss, but cannot run complex distributed coordinators

**Alternative rejected:** Two-phase commit would require:
- External transaction coordinator process
- Crash-recovery write-ahead log
- Complex startup protocols with log replay
- Resource overhead incompatible with decade-long operation on constrained hardware

**Philosophy:** MemoGarden prioritizes *survivability* (always starts, always recoverable) over *perfect consistency* (no data loss ever).

---

## 3. Operation Categories

MemoGarden operations fall into three transaction scope categories:

### 3.1 Simple Operations (Single Database)

Operations that affect only one database get standard SQLite ACID guarantees.

| Operation | Database | Example | Atomicity |
|-----------|----------|---------|-----------|
| `add_item()` | Soil only | Create Note, Message, Email | SQLite ACID |
| `add_system_relation()` | Soil only | Create structural fact | SQLite ACID |
| `add_entity()` | Core only | Create Artifact | SQLite ACID |
| `add_user_relation()` | Core only | Create engagement signal | SQLite ACID |

**Transaction semantics:** Standard SQLite BEGIN/COMMIT within single database.

### 3.2 Coordinated Operations (Both Databases)

Operations that must modify both databases atomically use application-level coordination.

| Operation | Databases | Soil Effect | Core Effect | Risk |
|-----------|-----------|-------------|-------------|------|
| `update_entity()` | Both | Write EntityDelta | Update Entity state + hash | 0.01%/year |
| `fossilize_relation()` | Both | Write fossilized relation | Delete active relation | 0.01%/year |

**Transaction semantics:** Best-effort atomicity with explicit operator-assisted recovery.

**Critical property:** These operations require explicit transaction:
```python
# Correct - explicit transaction
with mg.transaction():
    entity = mg.get_entity('core_abc')
    mg.update_entity('core_abc', {'content': '...'}, based_on_hash=entity.hash)

# Incorrect - will raise NotInTransactionError
entity = mg.get_entity('core_abc')
mg.update_entity('core_abc', {'content': '...'}, based_on_hash=entity.hash)
```

### 3.3 Split Operations (Sequential with Retry)

Operations where databases have dependency ordering, allowing graceful degradation.

| Operation | Pattern | Success Case | Failure Case |
|-----------|---------|--------------|--------------|
| `add_item() + add_user_relation()` | Item first, relation second | Both succeed | Item persists, relation retry |

**Transaction semantics:** Item creation commits independently. Relation creation retries on failure. Application can detect partial completion and resume.

**Rationale:** Facts are primary; relations are secondary. An Item without relations is valid (orphan). A relation without target Item is invalid (constraint violation).

---

## 4. Commit Protocol

### 4.1 Commit Ordering

**Soil commits first (source of truth), then Core.**

```python
def commit_transaction(self):
    """Best-effort atomicity across two databases."""
    soil_ok = False
    core_ok = False
    
    try:
        self._soil_conn.commit()
        soil_ok = True
        
        self._core_conn.commit()
        core_ok = True
        
    except Exception as e:
        self._log_commit_failure(soil_ok, core_ok, e)
        self._rollback_both()
        
        if soil_ok and not core_ok:
            self.status = SystemStatus.INCONSISTENT
        
        raise TransactionCommitError(soil_ok=soil_ok, core_ok=core_ok)
```

**Why Soil first:** Soil is the source of truth. Core state can be rebuilt from Soil deltas. If Core commits but Soil fails, we've lost the audit trail.

### 4.2 Failure Modes

| Scenario | Soil | Core | System State | Recovery |
|----------|------|------|--------------|----------|
| Both succeed | ✓ | ✓ | NORMAL | None needed |
| Both fail | ✗ | ✗ | NORMAL | Both rolled back cleanly |
| Soil commits, Core fails | ✓ | ✗ | **INCONSISTENT** | `memogarden repair` |
| Process killed between commits | ✓ | ✗ | **INCONSISTENT** | Detected on next startup |

**The critical failure mode:** Soil commits successfully, then system crashes before Core commits. This leaves:
- **Soil:** EntityDelta fact exists, pointing to Entity UUID
- **Core:** Entity state does not reflect the delta, hash chain broken

**Detection on startup:**
```python
def detect_orphaned_deltas():
    """Find EntityDeltas without corresponding Core updates."""
    deltas = soil.list_items(_type="EntityDelta")
    orphans = []
    
    for delta in deltas:
        entity = core.get_entity(delta.data['entity_uuid'])
        if entity.hash != delta.data['new_hash']:
            orphans.append(delta)
    
    return orphans
```

**Recovery:**
```bash
# Manual operator intervention
$ memogarden diagnose
INCONSISTENT: Found 1 orphaned EntityDelta
  - soil_abc123: update to core_xyz456 not applied

$ memogarden repair
Applying orphaned delta soil_abc123...
Entity core_xyz456 updated, hash verified
System status: NORMAL
```

### 4.3 Frequency Estimate

For personal system:
- **100 cross-database writes/day** (aggressive estimate)
- **100ms vulnerability window** per transaction (conservative)
- **Daily exposure:** 10 seconds
- **Annual exposure:** 3,650 seconds (~1 hour)
- **Failure probability:** Power loss ~once per 10,000 hours
- **Expected failures:** ~0.01% per year (~1 failure per 100 years)

**Acceptable risk:** Decade-long operation means ~0.1% cumulative probability. Operator-assisted recovery handles the rare case.

---

## 5. Transaction Lifecycle

### 5.1 Begin Transaction

```python
def begin_transaction(self):
    """Enter transaction mode. Locks both databases exclusively."""
    if self._in_transaction:
        raise AlreadyInTransactionError()
    
    # Lock Soil first
    self._soil_conn.execute("BEGIN EXCLUSIVE")
    try:
        # Then lock Core
        self._core_conn.execute("BEGIN EXCLUSIVE")
    except Exception as e:
        # If Core lock fails, rollback Soil
        self._soil_conn.rollback()
        raise
    
    self._in_transaction = True
```

**Properties:**
- One transaction per handle (no nesting)
- EXCLUSIVE locks prevent concurrent writes
- Other handles block on `busy_timeout` (5 seconds default)

### 5.2 Rollback Protocol

```python
def rollback_transaction(self):
    """Rollback both databases. Best-effort."""
    if not self._in_transaction:
        raise NotInTransactionError("rollback")
    
    try:
        self._soil_conn.rollback()
    except Exception as e:
        logger.error(f"Soil rollback failed: {e}")
    
    try:
        self._core_conn.rollback()
    except Exception as e:
        logger.error(f"Core rollback failed: {e}")
    
    self._in_transaction = False
```

**Best-effort rollback:** If commit succeeded on one database before failure, rollback is no-op on that database. System marked INCONSISTENT for operator intervention.

### 5.3 Context Manager

```python
@contextmanager
def transaction(self):
    """
    Context manager for transactions.
    Auto-commits on success, auto-rolls-back on exception.
    
    Note: Cross-database atomicity is best-effort. Rare failures
    (~0.01%/year) require operator intervention via `memogarden repair`.
    """
    self.begin_transaction()
    try:
        yield self
        self.commit_transaction()
    except Exception:
        self.rollback_transaction()
        raise
```

**Usage:**
```python
with mg.transaction():
    entity = mg.get_entity('core_abc')
    mg.update_entity('core_abc', {'content': '...'}, based_on_hash=entity.hash)
    # Auto-commits both DBs on exit
```

---

## 6. Two-Database Model Architecture

```
┌─────────────────────────────────────────────────┐
│  MemoGarden Handle  │
│                     │
│  ┌─────────────────┐  │
│  │ Transaction   │  │
│  │ Coordinator   │  │
│  └───────┬─────────┘  │
│          │          │
│    ┌─────┴────┐    │
│    │           │    │
└────┴───────────┴────┘
         │ Python imports
         ▼
┌─────────────────────────────────────────────────┐
│   Internal Python API                       │
│   (MemoGarden class)                        │
└───────────┬─────────────────────────────────┘
            │ SQLite
            ▼
┌─────────────────────────────────────────────────┐
│   Soil + Core databases                     │
└─────────────────────────────────────────────────┘
```

**Soil (immutable timeline):**
- Facts: Notes, Messages, Emails, ToolCalls, EntityDeltas
- System Relations: Immutable structural facts
- Fossilized User Relations: Archived engagement signals

**Core (mutable state):**
- Entities: Artifacts, Labels, Transactions
- Active User Relations: Current engagement signals
- ContextFrames: Attention tracking

---

## Transaction Implementation

### SQLite Configuration

```python
# Applied to both Soil and Core databases
PRAGMA journal_mode=WAL;           # Write-ahead logging
PRAGMA synchronous=FULL;           # Wait for fsync (crash-safe)
PRAGMA busy_timeout=5000;          # 5s timeout for lock conflicts
PRAGMA foreign_keys=ON;            # Enforce constraints
```

**Rationale:**
- WAL mode: Better concurrency, faster commits
- FULL synchronous: Survive power loss
- 5s timeout: Balance responsiveness vs. allowing long operations
- Foreign keys: Detect constraint violations early

### Isolation Level

**SERIALIZABLE** via explicit `BEGIN EXCLUSIVE` on both databases
(INV-TX-004); the begin/rollback/context-manager lifecycle lives in
§5.1–§5.3.

## Operation-Specific Semantics

### Single-Database Operations

**add_item():**
```python
def add_item(self, type: str, content: dict, /, *, metadata: dict = None) -> Item:
    """Create immutable Item in Soil."""
    # If in transaction, uses existing Soil connection
    # Otherwise, auto-commits
    
    uuid = f"soil_{uuid4()}"
    integrity_hash = sha256(json.dumps(content, sort_keys=True))
    
    self._soil_conn.execute("""
        INSERT INTO item (uuid, _type, realized_at, canonical_at, 
                         integrity_hash, fidelity, data, metadata)
        VALUES (?, ?, ?, ?, ?, 'full', ?, ?)
    """, (uuid, type, now(), now(), integrity_hash, 
          json.dumps(content), json.dumps(metadata)))
    
    if not self._in_transaction:
        self._soil_conn.commit()
    
    return Item(uuid=uuid, type=type, content=content, ...)
```

**Transaction behavior:**
- Within transaction: Defers commit
- Outside transaction: Auto-commits immediately

### Cross-Database Operations

**Design of record (2026-11-21 decision):** this transactional
`update_entity` + EntityDelta flow is the direction of travel. The
implementation currently bridges with in-place `update_hash`/`update_data`
edits (per-row sealed per Addendum v1.3) and is to converge to this
section's flow; the build is tracked in the memogarden-system repo.

**update_entity():**
```python
def update_entity(self, uuid: str, content: dict, /, 
                  *, based_on_hash: str) -> Entity:
    """
    Update Entity in Core, create EntityDelta in Soil.
    Requires explicit transaction.
    """
    if not self._in_transaction:
        raise NotInTransactionError(
            "update_entity requires explicit transaction"
        )
    
    # 1. Verify optimistic lock (hash matches)
    entity = self._get_entity_for_update(uuid)
    if entity.hash != based_on_hash:
        raise OptimisticLockError(
            expected=based_on_hash,
            actual=entity.hash,
            entity_uuid=uuid
        )
    
    # 2. Compute new hash
    new_hash = sha256(json.dumps(content, sort_keys=True) + entity.hash)
    
    # 3. Stage EntityDelta for Soil (written at commit)
    delta = {
        'entity_uuid': uuid,
        'previous_hash': entity.hash,
        'new_hash': new_hash,
        'changes': content,
        'applied_at': now()
    }
    self._pending_deltas.append(delta)
    
    # 4. Update Entity in Core
    self._core_conn.execute("""
        UPDATE entity 
        SET hash=?, previous_hash=?, version=version+1, 
            updated_at=?, state=?
        WHERE uuid=?
    """, (new_hash, entity.hash, now(), json.dumps(content), uuid))
    
    # 5. On commit, pending deltas written to Soil
    return Entity(uuid=uuid, hash=new_hash, content=content, ...)
```

**Atomicity guarantee:**
- Both DBs locked (EXCLUSIVE)
- EntityDelta written to Soil atomically with Entity update in Core
- On failure, both roll back (best-effort)

**Hash chain safety:**
- New hash computed but not stored until commit
- Impossible to extend chain with uncommitted hash
- Optimistic lock checked at commit time (re-validate hash)

**fossilize_relation():**
```python
def fossilize_relation(self, relation_uuid: str) -> None:
    """
    Move user relation from Core to Soil.
    Requires explicit transaction.
    """
    if not self._in_transaction:
        raise NotInTransactionError(
            "fossilize_relation requires explicit transaction"
        )
    
    # 1. Read relation from Core
    relation = self._core_conn.execute("""
        SELECT * FROM user_relation WHERE uuid=?
    """, (relation_uuid,)).fetchone()
    
    # 2. Insert into Soil (change UUID prefix)
    soil_uuid = f"soil_{relation_uuid[5:]}"  # core_ → soil_
    self._soil_conn.execute("""
        INSERT INTO system_relation 
        (uuid, kind, source, target, created_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (soil_uuid, relation['kind'], relation['source'], 
          relation['target'], relation['created_at'], 
          relation['metadata']))
    
    # 3. Delete from Core
    self._core_conn.execute("""
        DELETE FROM user_relation WHERE uuid=?
    """, (relation_uuid,))
    
    # On commit, both operations persisted atomically
```

### Split Operations

**add_item() + add_user_relation():**

Pattern for creating fact + engagement:

```python
# NOT in explicit transaction
try:
    # Step 1: Create Item (commits to Soil)
    item = mg.add_item('Note', {'text': '...'})
    
    # Step 2: Add relation (commits to Core)
    try:
        mg.add_user_relation(
            kind='explicit_link',
            source=context_uuid,
            target=item.uuid,
            horizon='+30d'
        )
    except Exception as e:
        # Item exists in Soil, relation failed
        # Retry or log for later
        logger.warning(f"Failed to add relation to {item.uuid}: {e}")
        # Operator can add relation later via repair

except Exception as e:
    # Item creation failed, nothing committed
    raise
```

**Rationale:**
- Facts are primary facts (source of truth)
- Relations are secondary (can be reconstructed)
- Item without relation is valid state (orphan)
- Relation without item would be invalid (dangling reference)

## Optimistic Locking

### Entity Hash Chains

Each Entity maintains a cryptographic hash chain:

```python
entity.hash = SHA256(JSON(entity.state) + entity.previous_hash)
```

**Properties:**
- Tamper detection: Recompute chain, verify hashes
- Optimistic locking: Update requires knowing current hash
- Full lineage: Trace back through previous_hash links

### Conflict Detection

```python
# Client 1
entity = mg.get_entity('core_abc')  # hash = H1
# ... time passes ...
mg.update_entity('core_abc', {...}, based_on_hash=H1)

# Client 2 (concurrent)
entity = mg.get_entity('core_abc')  # hash = H1
mg.update_entity('core_abc', {...}, based_on_hash=H1)

# One succeeds (hash → H2)
# Other fails: OptimisticLockError (expected H1, got H2)
```

### Conflict Resolution

```python
try:
    with mg.transaction():
        mg.update_entity('core_abc', content, based_on_hash=old_hash)
except OptimisticLockError as e:
    # Read current state
    current = mg.get_entity('core_abc')
    
    # Application decides:
    # - Retry with new hash (3-way merge)
    # - Abort and notify user
    # - Apply Last-Write-Wins policy
    
    # Example: Retry with new hash
    merged = three_way_merge(old_content, content, current.state)
    mg.update_entity('core_abc', merged, based_on_hash=current.hash)
```

**Policy:** MemoGarden provides detection, applications provide resolution strategy.

## Undo vs. Rollback

MemoGarden separates two concepts:

### Transaction Rollback

**Scope:** Current transaction  
**Timing:** Immediate (before commit)  
**Mechanism:** SQLite ROLLBACK  
**Effect:** Uncommitted changes discarded  

```python
with mg.transaction():
    mg.add_item('Note', {...})
    raise Exception()  # Auto-rollback, item not created
```

### Undo Operation

**Scope:** Committed operations  
**Timing:** Within 5 minutes of original operation  
**Mechanism:** Compensating ToolCall  
**Effect:** New facts reverse prior facts  

```python
# Create item (commits immediately)
item = mg.add_item('Note', {'text': 'wrong'})

# Undo within 5 minutes
mg.undo_tool_call(tool_call_uuid)
# Creates UndoToolCall Item
# Executes tool-specific undo logic
# May create new Facts (e.g., supersession)
```

**Tool support for undo:**
```python
class ToolDefinition:
    name: str
    supports_undo: bool
    undo_fn: Optional[Callable]

# Example
def undo_add_item(original_call: ToolCall, undo_call: ToolCall):
    """Undo item creation via supersession."""
    original_item = mg.get_item(original_call.result['uuid'])
    tombstone = mg.add_item('Tombstone', {
        'supersedes': original_item.uuid,
        'reason': 'undo'
    })
```

**Properties:**
- Undo respects Soil immutability (creates new facts, doesn't delete)
- Not all tools support undo
- After 5 minutes: Operator investigates delta log, performs manual mutations
- No redo capability

## Failure Modes & Recovery

### Startup Consistency Check

```python
def startup_check(soil_conn, core_conn):
    """Check for inconsistencies on startup."""
    issues = []
    
    # 1. Check for orphaned deltas
    orphans = soil_conn.execute("""
        SELECT ed.entity_uuid, ed.new_hash
        FROM item i
        JOIN json_extract(i.data, '$.entity_uuid') AS ed_uuid
        WHERE i._type = 'EntityDelta'
        AND NOT EXISTS (
            SELECT 1 FROM core.entity e
            WHERE e.uuid = ed_uuid
            AND e.hash = json_extract(i.data, '$.new_hash')
        )
    """).fetchall()
    
    if orphans:
        issues.append(ConsistencyIssue(
            type='orphaned_delta',
            count=len(orphans),
            details=orphans
        ))
    
    # 2. Check hash chain integrity
    broken_chains = core_conn.execute("""
        SELECT uuid FROM entity
        WHERE previous_hash IS NOT NULL
        AND previous_hash != (
            SELECT hash FROM entity AS prev
            WHERE prev.uuid = (
                SELECT entity_uuid FROM soil.item
                WHERE _type='EntityDelta'
                AND json_extract(data, '$.new_hash') = entity.previous_hash
                LIMIT 1
            )
        )
    """).fetchall()
    
    if broken_chains:
        issues.append(ConsistencyIssue(
            type='broken_hash_chain',
            count=len(broken_chains),
            details=broken_chains
        ))
    
    return issues
```

### Operating Modes

```python
class SystemStatus(Enum):
    NORMAL = "normal"           # Healthy operation
    INCONSISTENT = "inconsistent"  # Detected issues, still usable
    READ_ONLY = "readonly"      # Maintenance mode
    SAFE_MODE = "safe_mode"     # Degraded, diagnostics only
```

**Mode transitions:**
```
Startup:
- No issues → NORMAL
- Orphaned deltas detected → INCONSISTENT
- Database corruption → SAFE_MODE
- Operator flag → READ_ONLY

Runtime:
- NORMAL → INCONSISTENT: Commit failure (Soil ok, Core fail)
- Any → READ_ONLY: Operator sets mode
- INCONSISTENT → NORMAL: After successful repair
```

### Recovery Tools

**memogarden diagnose:**
```bash
$ memogarden diagnose

MemoGarden Diagnostics Report
=============================

System Status: INCONSISTENT

Issues Found:
1. Orphaned EntityDeltas: 3 deltas
   - Entity core_abc123: Delta points to hash H2, entity at H1
   - Entity core_def456: Delta points to hash H5, entity at H4
   - Entity core_ghi789: Delta points to hash H8, entity at H7

2. Database Health: OK
   - Soil: 1.2 GB, 0 integrity errors
   - Core: 450 MB, 0 integrity errors

3. Recent Errors:
   - 2026-02-02 14:23:15: Core commit failed (SQLITE_IOERR)

Recommended Actions:
- Run: memogarden repair rebuild-core
- Review: /var/log/memogarden/errors.log
```

**memogarden repair:**
```bash
$ memogarden repair rebuild-core

Rebuilding Core from Soil...
✓ Scanned 15,234 EntityDeltas in Soil
✓ Identified 3 out-of-sync entities
✓ Replayed deltas for core_abc123 (H1 → H2)
✓ Replayed deltas for core_def456 (H4 → H5)
✓ Replayed deltas for core_ghi789 (H7 → H8)
✓ Verified hash chains

Repair complete. System status: NORMAL
```

## Performance Characteristics

### Transaction Overhead

**Single-DB operations (no explicit transaction):**
- ~1ms overhead (BEGIN/COMMIT)
- Minimal for personal system workload

**Cross-DB operations (explicit transaction):**
- ~2-5ms overhead (two BEGINs, two COMMITs)
- 5s timeout on lock contention (rare)

**Optimistic lock conflicts:**
- Retry overhead: Re-read entity, recalculate hash
- ~2-3ms per retry
- Exponential backoff if multiple conflicts

### Concurrency

**Multiple handles, same process:**
- SQLite handles locking automatically
- busy_timeout prevents indefinite blocking
- EXCLUSIVE locks serialize cross-DB operations

**Multiple processes:**
- SQLite file locking prevents corruption
- Cross-DB atomicity may degrade (no process-level coordination)
- Acceptable for personal system (rare multi-process access)

**Typical workload (personal system):**
- 10-100 transactions/day
- Peak: 1-2 transactions/second (user actively working)
- Negligible contention with conservative locking

## Testing Strategy

### Unit Tests

```python
def test_transaction_rollback():
    """Verify both DBs rolled back on error."""
    with pytest.raises(ValueError):
        with mg.transaction():
            item = mg.add_item('Note', {'text': 'test'})
            mg.add_entity('Artifact', {'label': 'test'})
            raise ValueError("test error")
    
    # Verify neither DB has changes
    assert mg.get_item(item.uuid) is None
    assert mg.get_entity('core_test') is None

def test_optimistic_lock_conflict():
    """Verify conflict detection on concurrent updates."""
    entity = mg.get_entity('core_abc')
    
    # Simulate concurrent update
    mg.update_entity('core_abc', {'v': 2}, based_on_hash=entity.hash)
    
    # This should fail
    with pytest.raises(OptimisticLockError):
        mg.update_entity('core_abc', {'v': 3}, based_on_hash=entity.hash)
```

### Integration Tests

```python
def test_split_operation_resilience():
    """Verify Item created even if relation fails."""
    # Simulate Core database locked
    core_conn.execute("BEGIN EXCLUSIVE")
    
    item = mg.add_item('Note', {'text': 'test'})
    assert item.uuid.startswith('soil_')
    
    # Relation fails (Core locked)
    with pytest.raises(DatabaseLockError):
        mg.add_user_relation('explicit_link', ctx, item.uuid, '+30d')
    
    # Item still exists
    assert mg.get_item(item.uuid) is not None
    
    # Can add relation after unlock
    core_conn.rollback()
    mg.add_user_relation('explicit_link', ctx, item.uuid, '+30d')
```

### Chaos Testing

```python
def test_power_loss_during_commit():
    """Simulate process kill between Soil and Core commits."""
    # Monkey-patch commit to kill process
    original_commit = core_conn.commit
    def kill_after_soil():
        soil_conn.commit()
        os.kill(os.getpid(), signal.SIGKILL)
    
    core_conn.commit = kill_after_soil
    
    # This will kill process after Soil commits
    with pytest.raises(SystemExit):
        with mg.transaction():
            mg.update_entity('core_abc', {...}, based_on_hash=h1)
    
    # Restart and verify inconsistency detected
    mg2 = MemoGarden()
    assert mg2.status == SystemStatus.INCONSISTENT
    issues = mg2.diagnose()
    assert len(issues) == 1
    assert issues[0].type == 'orphaned_delta'
```

## Open Questions

1. **Multi-process coordination:** Should multiple processes coordinate cross-DB operations at application level, or rely purely on SQLite locking?
   - **Recommendation:** SQLite locking sufficient for personal system. Multi-process scenarios are rare.

2. **Automatic repair:** Should system attempt automatic repair on startup for simple inconsistencies (orphaned deltas)?
   - **Recommendation:** No. Conservative policy: Report, require operator approval.

3. **Metrics storage:** Should transaction metrics (commit latency, failures) be stored in Soil as SystemEvent Facts?
   - **Resolution:** No. Metrics stored in-memory + journald (not Soil). SystemEvent facts created only for significant operational events (transaction failure spikes, mode transitions). High-frequency performance metrics would pollute semantic data. See RFC-007 v2.1 Section 8.3 for full specification.

## References

- RFC-001 v5: Security & Operations Architecture
- RFC-002 v5: Relation Time Horizon & Fossilization
- RFC-005 v8: API Design
- RFC-006 v1: Error Handling & Diagnostics
- RFC-007 v2: Runtime Operations
- PRD v0.10.0: MemoGarden Personal Information System
- SQLite WAL mode: https://sqlite.org/wal.html
- SQLite transaction documentation: https://sqlite.org/lang_transaction.html

## Invariant Index (INV-TX)

Implementation code and `docs/cross-database-transactions.md` cite these
invariants by number; this table is their home:

| ID | Invariant | Section |
|---|---|---|
| INV-TX-001 | Single-DB operations use standard SQLite ACID | §3.1 |
| INV-TX-002 | Cross-DB operations use best-effort atomicity with app-level coordination | §2 |
| INV-TX-003 | Split operations commit independently; relation creation retries | §3.3 |
| INV-TX-004 | SERIALIZABLE via `BEGIN EXCLUSIVE` on both databases | §5.1 |
| INV-TX-005 | One transaction per handle (no nesting, no SAVEPOINTs) | §5.1 |
| INV-TX-006 | Other handles block on `busy_timeout` (5s default) | §5.1 |
| INV-TX-007 | Commit ordering: Soil first, then Core | §4.1 |
| INV-TX-008 | Soil commits but Core fails → INCONSISTENT | §4.2 |
| INV-TX-009 | Process killed between commits → INCONSISTENT (startup detection) | §4.2 |
| INV-TX-010 | Best-effort rollback (committed DB's rollback is a no-op) | §5.2 |
| INV-TX-011 | Entity hash is a per-row integrity seal | §Entity Hash Chains; Addendum v1.3 |
| INV-TX-012 | Update requires `based_on_hash` to match current hash | §Conflict Detection |
| INV-TX-013 | Hash mismatch → `OptimisticLockError` | §Conflict Detection |
| INV-TX-014 | Operating modes: NORMAL, INCONSISTENT, READ_ONLY, SAFE_MODE | §Operating Modes |
| INV-TX-015 | No issues → NORMAL | §Operating Modes |
| INV-TX-016 | Orphaned deltas → INCONSISTENT | §Operating Modes |
| INV-TX-017 | Database corruption → SAFE_MODE | §Operating Modes |
| INV-TX-018 | Startup checks for orphaned EntityDeltas | §Startup Consistency Check |
| INV-TX-019 | Startup checks hash integrity — per-row recompute per Addendum v1.3 (the pre-v1.3 cross-row chain check is retired) | §Startup Consistency Check |
| INV-TX-020 | System starts regardless of state (always-available startup) | §Startup Consistency Check |

## Revision History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-02-02 | Initial specification |
| 1.1 | 2026-02-06 | Resolved Open Question #3: Metrics storage location (in-memory + journald, not Soil) |
| 1.3 | 2026-11-20 | Addendum — hash integrity semantics re-ratified as a per-row integrity seal (`previous_hash` = chained value, not a cross-row link); `entity_update_timestamp` trigger guarded; migration 20261120→20261121 re-signs entities; seed stamp 20261121 (memogarden-system#6). Header version aligned to the addendum. |
| 1.4 | 2026-11-21 | Coherence review: §4/§5 duplicates inside "Transaction Implementation" merged (SQLite pragmas + isolation pointer kept); INV-TX-001..020 invariant index added (the numbering the implementation cites); §Cross-Database Operations reaffirmed as design of record — decision 2026-11-21: the transactional `update_entity` + EntityDelta flow is the direction of travel, current in-place edits are the bridge to converge (build tracked in memogarden-system). |

---

**Status:** Draft  
**Next Steps:**
1. Review transaction coordination mechanism
2. Review failure mode handling
3. Implement startup consistency checks
4. Implement transaction API with context manager
5. Test power-loss scenarios
6. Document operator recovery procedures

---

---

## Addendum v1.3 (2026-11-20, memogarden-system#6): Hash Integrity Semantics

The original hash-chain description above ("Entity Hash Chains",
"Full lineage: Trace back through previous_hash links", and the
consistency-check SQL resolving previous_hash against other entity rows)
described an invariant the writers never established. Ratified instead:

**The entity hash is a per-row integrity seal.**

```
entity.hash = SHA256(lifecycle fields + previous_hash)
```

- `previous_hash` stores the previous version's hash VALUE — the link
  chained into the current hash. In-place edits (`entity.update_hash` /
  `update_data`, the sanctioned edit path behind the Semantic API, triage
  relabels, scope stamping) overwrite that value in the same row by
  design; it is NOT resolvable against other entity rows, and no writer
  path ever produced a cross-entity link. The old "broken chain"
  (previous_hash resolvability) check therefore false-positived on every
  edited entity (12,713 of 21,325 live rows by 2026-11-20) and was
  retired in favor of per-row recomputation:
  `hash == SHA256(row lifecycle fields + row.previous_hash)`.
- `supersede` now records the pre-supersede hash in `previous_hash`
  (previously the column was left stale, so superseded rows were also
  unverifiable).
- The `entity_update_timestamp` trigger is guarded
  (`WHEN NEW.updated_at = OLD.updated_at`): sanctioned writers set
  `updated_at = isodatetime.now()` and chain that exact value into the
  hash, but the unguarded trigger stomped the column to
  `datetime('now')` afterwards, desynchronizing hash and fields for
  every updated row. Writers that leave updated_at unset keep the
  auto-touch behavior.
- Entity `data` is NOT part of the hash (unchanged); content audit
  remains in Soil facts and EntityDeltas.
- Migration `20261120 -> 20261121` re-signs every entity once from its
  stored fields: pre-2026-11 hashes predate the current field format
  (99.3% of live rows) and were unverifiable by construction, so
  re-signing loses no detectable history. The schemas seed stamp bumps
  to 20261121 so fresh databases are born at the new semantics.

**What this buys:** SAFE_MODE now means real tampering (any mutation of
`type`/`created_at`/`updated_at`/`group_id`/`derived_from`/
`superseded_by`/`superseded_at`/`previous_hash`/`hash` outside the
writers breaks the seal) instead of permanent false alarm. What it does
not buy: cross-version lineage tracing and entity-`data` integrity —
lineage lives in supersede references and EntityDeltas; data integrity
is Soil's domain.

---

**END OF RFC**
