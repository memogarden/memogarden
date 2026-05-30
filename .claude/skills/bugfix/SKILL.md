---
name: bugfix
description: Complete bugfix workflow from investigation to PR. Use when investigating, diagnosing, and fixing bugs following systematic debugging practices.
---

# Bugfix Workflow

This skill defines the complete workflow for fixing bugs from initial investigation to merged PR. Follow this process for all bugfix work.

## When to Use This Skill

Use this skill when:
- Investigating reported bugs or unexpected behavior
- Diagnosing root causes of issues
- Implementing fixes for identified problems
- Following the complete bugfix lifecycle
- Addressing regressions or production issues

## Workflow Overview

**Phase 0: Pre-Work Test Baseline**
- Run tests to establish baseline
- Document pre-existing failures
- Set scope boundaries for test fixes

↓

**Phase 1: Bug Investigation & Diagnosis**
- Reproduce the bug consistently
- Gather symptoms and context
- Form hypotheses about root cause
- Narrow down through systematic testing

↓

**Phase 2: Root Cause Analysis**
- Identify the actual cause (not just symptoms)
- Understand why the bug occurs
- Determine minimal, targeted fix
- Consider broader implications

↓

**Phase 3: Solution Development & Testing**
- Create bugfix branch from main
- Implement minimal fix for identified issue
- Add tests to prevent regression
- Verify fix resolves problem without side effects

↓

**Phase 4: Post-Fix Test Validation**
- Run tests to compare vs baseline
- Fix test failures caused by our changes
- Document unrelated failures for user awareness
- Ensure no regressions introduced

↓

**Phase 5: CI Integration & Completion**
- Create PR to main branch
- Include detailed bug analysis and fix explanation
- Monitor CI status and address issues
- Alert human for review and merge

## Phase 0: Pre-Work Test Baseline

**Critical First Step**: Before starting any bugfix work, establish a test baseline to understand what's already broken.

### Why Test Baseline Matters

- **Prevents scope creep**: You won't accidentally fix unrelated test failures
- **Measures impact**: Know if your changes made things worse or better  
- **Sets boundaries**: Clear scope for what tests you're responsible for fixing
- **Documents reality**: Baseline evidence of pre-existing issues

### Establish Baseline Process

```bash
# Run full test suite and capture baseline
pytest tests/ -v > test_baseline.txt 2>&1

# Extract summary
tail -20 test_baseline.txt

# Count failures/errors
echo "Baseline: $(grep -c 'FAILED\|ERROR' test_baseline.txt) issues"
```

### Document Pre-Existing Failures

**Create Baseline Record:**
```markdown
## Test Baseline - [Date]

**Pre-existing failures** (NOT our responsibility):
- test_name_1: [brief description of failure]
- test_name_2: [brief description of failure]

**Baseline summary**: X passed, Y failed, Z errors
**Scope**: Only fixing tests directly related to [bug description]
```

### Test Fix Scope Rules

**Tests ARE allowed to fail when:**

1. **Pre-existing failures**: Tests were already failing at baseline
   - Example: GUI tests failing due to Playwright setup issues
   - Action: Document and skip these tests
   - Don't attempt to fix unless explicitly part of bugfix scope

2. **Unrelated scope**: Fix would be different work/PR
   - Example: Bugfix is schema drift, but template test needs content update
   - Action: Inform user about unrelated failure
   - Recommend separate PR/issue for the problem

3. **User decision**: User consciously skips tests
   - Example: Major refactor needed, test not suitable for CI
   - Action: Document user's reasoning for exclusion
   - Add test to CI skip list with explanation

**Tests MUST be fixed when:**

- **Our changes broke them**: Test failures introduced by our bugfix
- **Directly related**: Test failures directly related to the bug being fixed
- **Regression detection**: Tests that verify our fix doesn't break existing behavior

### Baseline Documentation Template

```bash
# Before starting bugfix work
cat > BUGFIX_SCOPE.md << 'EOF'
# Bugfix Scope: [Bug Title]

## Test Baseline
**Date**: [Timestamp]
**Baseline**: X passed, Y failed, Z errors

## Pre-existing Failures (Do NOT Fix)
- test_gui_example: Playwright setup issue
- test_integration_test: Missing external dependency

## In-Scope Test Fixes
- Tests directly related to [our bug]
- Tests broken by our changes
- Regression tests for our fix

## Out-of-Scope (Inform User)
- unrelated_test_failure: Different issue, recommend separate PR
EOF
```

### Post-Fix Validation

After implementing the fix, run tests again:

```bash
# Compare against baseline
pytest tests/ -v > test_postfix.txt 2>&1

# Check if we made things worse
echo "Post-fix: $(grep -c 'FAILED\|ERROR' test_postfix.txt) issues"

# Compare results
diff -u test_baseline.txt test_postfix.txt
```

**Success Criteria:**
- ✅ No NEW test failures introduced
- ✅ All related tests now pass
- ✅ Pre-existing failures unchanged (or improved)

## Phase 1: Bug Investigation & Diagnosis

### Initial Investigation

**Systematic Approach:**

1. **Reproduce the bug consistently**
   - Get exact steps to reproduce
   - Identify minimal reproduction case
   - Confirm it's not expected behavior
   - Document error messages, stack traces, or unexpected outputs

2. **Gather symptoms and context**
   - When does the bug occur? (timing, conditions)
   - What changed recently? (commits, configuration)
   - What are the symptoms? (visible vs underlying)
   - What components are involved?

3. **Form hypotheses**
   - Start with most likely causes
   - Consider recent changes
   - Think about data flow and state
   - List potential failure points

4. **Narrow down through testing**
   - Test each hypothesis systematically
   - Use debugging tools (logs, breakpoints, database queries)
   - Isolate the problem area
   - Confirm exact trigger point

### Investigation Tools

**Database Investigation:**
```bash
# Check data state
sqlite3 database.db "SELECT * FROM table WHERE condition"

# Verify data integrity
sqlite3 database.db "PRAGMA integrity_check"

# Check recent changes
sqlite3 database.db "SELECT * FROM table ORDER BY timestamp DESC LIMIT 10"
```

**Log Investigation:**
```bash
# Check application logs
tail -f /var/log/app.log

# Search for specific errors
grep "ERROR" /var/log/app.log

# Check recent errors
find /var/log -name "*.log" -mtime -1 -exec grep ERROR {} \;
```

**State Investigation:**
```bash
# Check configuration
cat config.json

# Verify environment variables
env | grep APP_

# Check file permissions
ls -la /path/to/file
```

### Common Bug Patterns

**Schema Drift:**
- Configuration files out of sync with code
- Database schema mismatches
- State file version incompatibilities
- Cache corruption

**Race Conditions:**
- Timing-dependent failures
- Concurrent access issues
- Async/sync mismatches
- State inconsistency

**Data Issues:**
- Unexpected data formats
- Missing required fields
- Type mismatches
- Boundary conditions

## Phase 2: Root Cause Analysis

### Identify the Actual Cause

**Questions to Answer:**
- What is the exact trigger point?
- Why does this behavior occur?
- What assumption is being violated?
- What edge case isn't handled?

**Example Analysis:**
```
Symptom: Agent sees 101 unread messages on app restart
Investigation: Checked chat state (last_read_id: 168) vs messages (max ID: 168)
Hypothesis: Schema version mismatch causing incorrect unread calculation
Testing: Compared persisted schema.json vs code schema definition
Root Cause: Schema files created once but never updated when code evolved
```

### Determine Fix Strategy

**Minimal Fix Principles:**
- Fix the root cause, not just symptoms
- Make the smallest change that solves the problem
- Avoid introducing new complexity
- Consider future maintainability

**Fix Categories:**
1. **Data Fix**: Update data/state without code changes
2. **Logic Fix**: Correct algorithm or business logic
3. **Validation Fix**: Add missing checks or guards
4. **Update Fix**: Handle version mismatches and migrations
5. **Configuration Fix**: Adjust settings or defaults

### Consider Broader Implications

**Before Implementing:**
- Are there other places with similar issues?
- Could this fix introduce new bugs?
- Does this affect backward compatibility?
- Should we add tests to prevent regression?

## Phase 3: Solution Development & Testing

### Branch Creation

```bash
# Bugfix branch naming
git checkout main
git pull
git checkout -b bugfix/descriptive-name

# Examples
bugfix/schema-version-mismatch
bugfix/null-pointer-exception
bugfix/database-connection-leak
```

### Implementation Process

**1. Implement the minimal fix**
```python
# Example: Schema version checking
def _initialize_state(self) -> None:
    """Initialize state file and schema if they don't exist or are outdated."""
    # ... existing code ...
    
    # Always ensure schema is up-to-date
    current_schema = self.get_schema()
    schema_needs_update = False

    if self.schema_file.exists():
        try:
            existing_schema = json.loads(self.schema_file.read_text())
            existing_version = existing_schema.get("version")
            if existing_version != current_schema.get("version"):
                # Schema version mismatch - update schema
                schema_needs_update = True
        except (json.JSONDecodeError, IOError):
            # Corrupted schema file - recreate
            schema_needs_update = True
    else:
        # Schema file doesn't exist - create it
        schema_needs_update = True

    if schema_needs_update:
        self.schema_file.write_text(json.dumps(current_schema, indent=2))
```

**2. Add regression tests**
```python
def test_schema_auto_update_on_version_mismatch():
    """Test that schema files auto-update when version changes."""
    # Create app with v1 schema
    app = ChatApp()
    old_schema = app.get_schema()
    old_schema["version"] = 1
    app.schema_file.write_text(json.dumps(old_schema, indent=2))
    
    # Reinitialize app (should update to v2)
    app._initialize_state()
    
    # Verify schema was updated
    current_schema = json.loads(app.schema_file.read_text())
    assert current_schema["version"] == 2
```

**3. Test the fix thoroughly**
```bash
# Run related tests
pytest tests/test_specific_bug.py -v

# Run full test suite
pytest tests/ -v

# Check for regressions
pytest tests/ --cov
```

### Testing Strategy

**Reproduce Bug Before Fix:**
```python
def test_bug_reproduction():
    """Verify the bug exists before fixing."""
    # Setup conditions that trigger bug
    result = function_with_bug()
    assert result == "wrong_behavior"  # Confirms bug exists
```

**Verify Fix After Implementation:**
```python
def test_bug_fix():
    """Verify the fix resolves the issue."""
    # Setup same conditions
    result = function_with_fix()
    assert result == "correct_behavior"  # Confirms fix works
```

**Test Edge Cases:**
```python
def test_edge_cases():
    """Test edge cases around the fix."""
    # Test boundary conditions
    # Test with null/empty values
    # Test with concurrent access
    # Test with malformed input
```

## Phase 4: Post-Fix Test Validation

**After implementing the fix, run tests to validate against the established baseline.**

### Run Test Suite

```bash
# Run full test suite
pytest tests/ -v

# Or use project-specific test runner
./run_tests.sh
```

### Compare Against Baseline

**Check for new failures:**
```bash
# Count current failures
current_failures=$(pytest tests/ -v 2>&1 | grep -c 'FAILED\|ERROR')

# Compare with baseline
echo "Baseline failures: $baseline_failures"
echo "Current failures: $current_failures"

# Should not have MORE failures than baseline
if [ $current_failures -gt $baseline_failures ]; then
    echo "WARNING: Introduced new test failures!"
fi
```

### Categorize Test Failures

**For each failure, determine:**

1. **Caused by our changes?** → **MUST FIX**
   - Our fix broke existing tests
   - Tests need updating for our changes
   - Directly related to the bug we're fixing

2. **Pre-existing at baseline?** → **SKIP (document)**
   - Already failing in baseline
   - Not our responsibility to fix
   - Document in baseline record

3. **Unrelated to our work?** → **INFORM USER**
   - Different issue than what we're fixing
   - Would be scope creep to fix now
   - Recommend separate PR/issue

### Fixing Our Test Failures

**Process for fixing tests we broke:**

1. **Understand why the test fails**
   ```bash
   # Run specific failing test with details
   pytest tests/test_specific.py::test_name -xvs
   ```

2. **Determine appropriate fix**
   - Test needs updating for new behavior?
   - Test caught a real regression in our fix?
   - Test was already fragile/unreliable?

3. **Apply minimal fix**
   - Update test assertions to match new expected behavior
   - Fix regression in our implementation
   - Add test skip/warning if appropriate

4. **Verify fix**
   ```bash
   # Re-run the specific test
   pytest tests/test_specific.py::test_name -xvs
   
   # Re-run full suite to ensure no new breakage
   pytest tests/ -v
   ```

### Document Test Changes

**For each test fix, document:**
```markdown
## Test Fix: test_name

**Reason**: Test failed after implementing schema version checking
**Root Cause**: Test using old schema with 'read' column
**Fix**: Updated test to use current schema and app-state tracking
**Impact**: Test now passes, verified no regressions
```

### Scope Boundary Examples

**✅ In-Scope (Should Fix):**
- Tests failing because we changed the API they test
- Tests that need updating to match our fix
- Regression tests that catch bugs in our implementation

**❌ Out-of-Scope (Document & Skip):**
- GUI tests that were already failing at baseline
- Integration tests with external dependencies
- Performance tests requiring environment setup

**⚠️ Inform User (Ask for guidance):**
- Tests that fail for unrelated reasons
- Tests requiring major refactoring to fix
- Tests that might indicate deeper architectural issues

### Commit Test Fixes

```bash
# Commit test fixes separately if substantial
git add tests/
git commit -m "fix(tests): update tests for [bug description]

Fixed test failures caused by [our changes].

**Changes:**
- test_name: Description of change
- test_name_2: Description of change

**Root Cause**: Tests needed updating for [new behavior/schema]

**Impact**: 
- ✅ X tests now pass
- ✅ No new regressions introduced
- ✅ Matches baseline + expected improvements"
```

### Validation Checklist

Before proceeding to CI:

- [ ] **Baseline comparison**: No more failures than baseline
- [ ] **Our fixes**: All tests we broke are now fixed  
- [ ] **Documentation**: Test changes documented
- [ ] **Regressions**: No new bugs introduced
- [ ] **Scope**: Only fixed tests related to our changes

## Phase 5: CI Integration & Completion

### Commit the Fix

```bash
git add src/affected/files
git commit -m "fix(component): brief description of bug

Detailed explanation of what was fixed:

**Problem**: [What was broken and why]
**Root Cause**: [Actual cause of the bug]
**Solution**: [How the fix addresses the root cause]
**Impact**: [What behavior changes, if any]

**Changes:**
- File1: Description of changes
- File2: Description of changes

**Tests:**
- Added test for bug reproduction and fix
- Verified no regressions in existing functionality

Fixes #[issue-number]"
```

### Create Pull Request

```bash
# Push bugfix branch
git push -u origin bugfix/your-bugfix-name

# Create PR
gh pr create --title "fix(component): brief bug description" --base main
```

**PR Description Template:**
```markdown
## Problem

[Describe the bug: what was broken, symptoms, impact]

**Example**: Agents saw 101 unread messages on app restart instead of correct count.

## Root Cause Analysis

[Explain the investigation process and actual cause]

**Investigation Steps:**
1. Checked chat state (last_read_id: 168) vs messages (max ID: 168)  
2. Compared persisted schema.json vs code schema definition
3. Identified schema version mismatch

**Root Cause**: Schema files created once but never updated when code evolved, causing version drift between persisted state and current code.

## Solution

[Describe the fix and how it addresses the root cause]

**Changes:**
- Added schema version check in `_initialize_state()`
- Schema files now auto-update when version mismatches are detected
- Handles corrupted schema files gracefully (recreates them)
- Prevents stale schema files from persisting across app restarts

## Impact

- ✅ Fixes the "101 unread messages" bug
- ✅ Ensures schema files always match current code version
- ✅ Prevents future schema drift issues
- ✅ No breaking changes or API modifications

## Testing

- [x] Bug reproduction test added
- [x] Fix verification test passing
- [x] All existing tests passing
- [x] No regressions introduced
- [x] Edge cases tested

## Verification

**Before Fix**: [describe broken behavior]
**After Fix**: [describe correct behavior]

Tested with:
- Local development environment
- Production-like configuration
- Edge cases and boundary conditions

## Related

- GitHub Issue #[number]
- Related to PR #[number] (if applicable)
- Documentation updates (if behavior changed)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

### Monitor CI Status

```bash
# Check PR status
gh pr view
gh pr checks

# Watch for CI results
gh run list --branch bugfix/your-name

# Check specific run
gh run view <run-id>
```

### Handle CI Failures

**Simple Fixes** (apply directly):
- Test assertion updates (when fix changes behavior)
- Missing test files in commit
- Import path corrections
- Configuration adjustments

**Escalate to Human** (complex issues):
- Test design issues
- Environment-specific failures  
- Intermittent/flaky tests
- Performance regressions

## Phase 5: Completion & Documentation

### PR Readiness Checklist

Before alerting the human, verify:

- [ ] **Bug is fixed**
  - Minimal reproduction case now works correctly
  - Root cause addressed, not just symptoms
  - No side effects from the fix

- [ ] **Tests are comprehensive**
  - Bug reproduction test included
  - Fix verification test passing
  - Regression tests added
  - Edge cases covered

- [ ] **No regressions**
  - All existing tests passing
  - No new warnings or errors
  - Performance not degraded

- [ ] **Documentation updated** (if needed)
  - Behavior changes documented
  - Migration guides if breaking changes
  - API documentation if external changes

### Update Documentation (If Needed)

**When Documentation Changes Are Needed:**
- Behavior changes affect users
- API endpoints modified
- Configuration requirements changed
- New error conditions introduced

**Update Locations:**
- README files
- API documentation  
- User guides
- Changelog

### Alert Human

When bugfix is ready for review:

```bash
# Get PR URL
gh pr view --json url --jq .url
```

**Alert Template:**
```
Bugfix ready for review: {PR_URL}

🐛 Bug: {Brief description}
✅ Root cause identified and fixed
✅ Regression tests added
✅ All tests passing
✅ No side effects detected

**Impact**: {What users will notice}
**Risk**: {Low/Medium/High - explain why}
```

## Common Pitfalls

### Investigation Issues

**❌ Fixing symptoms instead of root cause**
- Example: Hiding error messages instead of fixing the error
- Fix: Always trace back to the actual source of the problem

**❌ Incomplete investigation**
- Example: Making assumptions without testing
- Fix: Systematically test each hypothesis

**❌ Ignoring recent changes**
- Example: "This code hasn't changed in years"
- Fix: Check recent commits, configuration changes, data updates

### Solution Issues

**❌ Over-engineering the fix**
- Example: Adding complex validation when simple check suffices
- Fix: Use the minimal fix that solves the problem

**❌ Introducing new bugs**
- Example: Fix breaks other functionality
- Fix: Run full test suite and check for regressions

**❌ Breaking backward compatibility**
- Example: Fix changes API contract
- Fix: Consider migration path or alternative approach

### Testing Issues

**❌ Not reproducing the bug first**
- Example: Writing fix without confirming bug exists
- Fix: Always write reproduction test first

**❌ Missing regression tests**
- Example: Fix tested but similar cases not covered
- Fix: Add tests for edge cases and related scenarios

**❌ Fragile tests**
- Example: Tests depend on exact timing or external state
- Fix: Make tests deterministic and isolated

## Related Skills

- **memogarden-debugging**: Detailed debugging workflows
- **memogarden-testing**: Testing philosophy and practices  
- **feature-development**: Feature development workflow
- **change-commit**: Git commit workflow
- **process-improvement**: Learning from mistakes

## Quick Reference

**Start new bugfix:**
1. **Establish test baseline**: Run tests, document pre-existing failures
2. Reproduce and investigate the bug
3. Identify root cause systematically
4. `git checkout -b bugfix/name`
5. Implement minimal fix
6. Add reproduction and regression tests
7. **Post-fix validation**: Run tests, fix only what we broke
8. `git add && git commit`
9. `git push && gh pr create`
10. Monitor CI, fix simple issues
11. Alert human when ready

**Test baseline commands:**
```bash
# Establish baseline
pytest tests/ -v > test_baseline.txt 2>&1
echo "Baseline: $(grep -c 'FAILED\|ERROR' test_baseline.txt) issues"

# Post-fix comparison
pytest tests/ -v > test_postfix.txt 2>&1
echo "Post-fix: $(grep -c 'FAILED\|ERROR' test_postfix.txt) issues"

# Check for new failures
diff -u test_baseline.txt test_postfix.txt
```

**Investigation commands:**
```bash
# Database investigation
sqlite3 database.db "SELECT * FROM table WHERE condition"

# Log investigation
grep "ERROR" /var/log/app.log

# State investigation
cat config.json
```

**Bugfix commit template:**
```bash
git commit -m "fix(component): description

**Problem**: What was broken
**Root Cause**: Actual cause  
**Solution**: How fix addresses it
**Impact**: Behavior changes

Fixes #[issue-number]"
```

---

**Last Updated:** 2026-05-30  
**For:** AI agents working on bugfixes  
**Maintained by:** Project contributors