---
name: feature-development
description: Complete feature development workflow from issue to PR. Use when implementing new features following the team's established development process.
---

# Feature Development Workflow

This skill defines the complete workflow for developing features from initial issue to merged PR. Follow this process for all feature development work.

## When to Use This Skill

Use this skill when:
- Implementing new features or functionality
- Making significant changes to existing features
- Working on user-facing changes
- Following the complete feature development lifecycle

## Workflow Overview

**Phase 1: Issue Creation & Discussion**
- Create GitHub issue describing problem
- Discuss solution approach if needed
- Get alignment on requirements before implementation

↓

**Phase 2: Branch Creation & Implementation**
- Create feature branch from main
- Follow continue-implementation skill for execution
- Make changes, write tests, update docs

↓

**Phase 3: Quality Gates**
- Documentation review: accuracy and completeness
- Test coverage review: fidelity and organization
- Requirements review: reflect actual implementation

↓

**Phase 4: CI Integration & Monitoring**
- Create PR to main branch
- Monitor CI status in PR
- Fix obvious CI issues; escalate complex ones

↓

**Phase 5: Completion & Alert**
- Verify CI passes and PR is ready
- Alert human for review and merge

## Phase 1: Issue Creation & Discussion

### GitHub Issue Requirements

Every feature should start with a GitHub issue that includes:

**Required Elements:**
- Clear problem statement
- Proposed solution (if discussed)
- Success criteria
- Relevant context/links to specs or requirements

**Issue Template:**
```markdown
## Problem Statement
[Describe the problem to solve]

## Proposed Solution
[Describe the solution approach - if already discussed]

## Success Criteria
- [ ] Criterion 1
- [ ] Criterion 2

## Context
[Links to relevant specs, requirements docs, or discussions]
```

### Pre-Implementation Discussion

- **If solution is clear**: Proceed directly to implementation
- **If solution needs discussion**: Get alignment on approach before branching
- **Check for existing issues**: Avoid duplicate work

## Phase 2: Branch Creation & Implementation

### Branch Naming

```bash
# Feature branches
feature/descriptive-name
feature/feature-name

# Example
feature/document-management-system
feature/archived-documents-ux
feature/ci-workflow
```

### Implementation Process

**Use the continue-implementation skill for detailed execution:**

1. **Create feature branch**
   ```bash
   git checkout main
   git pull
   git checkout -b feature/your-feature-name
   ```

2. **Follow continue-implementation skill**
   - Execute implementation plan
   - Write/update tests
   - Update documentation
   - Run tests frequently

3. **Commit frequently with clear messages**
   ```bash
   git add .
   git commit -m "feat: add document management system"
   ```

### Tool Registration (Agent-Based Projects)

**For projects that add tools to AI agents**, additional steps are required:

**⚠️ Important: Smoke tests do NOT verify tool-handler registration!**

Smoke tests only check that tools can be loaded from JSON definitions. They do NOT verify that:
- Handler functions exist
- Handler functions are properly registered
- Tool schemas match handler signatures
- Tools appear in agent context (AUI rendering)

**Required Registration Steps:**

1. **Add handler function to handlers module**
   ```python
   # In src/tools/handlers.py or equivalent
   def handle_<tool_name>(conn, invocation_id, ...):
       """Handler function for <tool_name> tool."""
       # Implementation
       return {"output": result, "error": None, "stop": False}
   ```

2. **Add handler to agent imports**
   ```python
   # In src/mars/agent.py or equivalent
   from tools.handlers import (
       handle_existing_tool,
       handle_<tool_name>,  # Add this line alphabetically
       ...
   )
   ```

3. **⚠️ CRITICAL: Update AUI tool rendering (if applicable)**
   ```python
   # In src/tools/loader.py or equivalent
   # Check for category_order list and add new category
   category_order = ["system", "chat", "tasks", "files", "documents", "web", "skills"]
   #                                                                       ^^^^^^^ ADD NEW CATEGORIES HERE
   ```
   
   **Why this matters:** The `get_aui_tools()` function has a hardcoded `category_order` list. If you add a new tool category but forget to update this list, the tools will be registered but won't appear in agent context (silent failure).

4. **Verify registration manually**
   ```python
   # Test handler is registered
   from mars.agent import _TOOL_HANDLERS
   assert '<tool_name>' in _TOOL_HANDLERS
   
   # Test tools appear in agent context
   from tools.loader import get_aui_tools
   aui_tools = get_aui_tools()
   assert f'%%% tools:<new_category>' in aui_tools
   ```

**Why This Happens:**

Agent projects typically have:
- **tools.json**: Tool definitions (schema, description, parameters)
- **handlers.py**: Handler function implementations
- **agent.py**: Dynamic handler mapping via `_build_tool_handlers()`
- **loader.py**: AUI tool rendering with hardcoded category list

The agent imports handlers explicitly AND builds a handler mapping dynamically. Missing explicit imports or category updates causes silent failures.

**Common Pitfalls:**
- ✗ Adding tools to tools.json but forgetting to update category_order
- ✗ Adding handlers but not importing them in agent setup
- ✗ Assuming smoke tests verify registration (they don't)
- ✗ Hardcoding expected tool counts in tests instead of using schema

**Verification Tests:**
- Look for tests like `test_register_tools_from_spec` in test_agent.py
- These tests verify tool-handler coverage but are NOT smoke tests
- Pre-commit smoke tests won't catch missing handler registrations or missing categories

## Phase 3: Quality Gates

Before creating a PR, verify these quality gates:

### Documentation Review

**Check: Is documentation accurate and up to date?**

- [ ] **Requirements reflect actual implementation**
  - Did requirements change during implementation?
  - Do specs match what was actually built?
  - Are decisions documented?

- [ ] **API documentation updated**
  - New endpoints documented?
  - Changed endpoints updated?
  - Breaking changes noted?

- [ ] **Architecture decisions recorded**
  - Why this approach?
  - Alternatives considered?
  - Trade-offs documented?

- [ ] **User-facing changes documented**
  - New features explained?
  - Behavior changes noted?
  - Migration guides if needed?

### Test Coverage Review

**Check: Do tests adequately test for fidelity to requirements?**

- [ ] **Unit tests**
  - New functions have tests?
  - Edge cases covered?
  - Error conditions tested?

- [ ] **Integration tests**
  - Component interactions tested?
  - Database operations tested?
  - API contracts tested?

- [ ] **Smoke tests**
  - Critical paths work?
  - Basic functionality verified?
  - No regressions in core features?

- [ ] **Test organization**
  - Tests properly categorized (unit/integration/smoke)?
  - Test names are descriptive?
  - Tests are independent and robust?

- [ ] **Test quality**
  - Tests check behavior, not implementation?
  - Tests are maintainable?
  - No brittle tests that break easily?

- [ ] **⚠️ NO HARDCODED COUNTS**
  - Tests use schema-based validation instead of hardcoded numbers
  - Example: `assert len(registered_tools) == len(get_tool_names())`
  - NOT: `assert len(registered_tools) == 27`
  - This prevents test breakage when tools are added/removed

### Requirements Review

**Check: Do requirements and specs reflect decisions made?**

- [ ] **Original requirements met**
  - Does implementation solve the stated problem?
  - Are success criteria achieved?

- [ ] **Scope changes documented**
  - Did requirements change during development?
  - Are new requirements documented?
  - De-scoped items noted?

- [ ] **Technical decisions recorded**
  - Why specific approaches were chosen
  - Performance considerations
  - Security considerations

## Phase 4: CI Integration & Monitoring

### PR Creation

```bash
# Push feature branch
git push -u origin feature/your-feature-name

# Create PR
gh pr create --title "feat: Your Feature Title" --base main
```

**PR Description Template:**
```markdown
## Summary
[One-line description of what this PR does and why]

## Problem Statement
[What problem does this solve? What was the current limitation?]

## Solution
[How does this PR solve the problem? What approach was taken?]

## Changes
- **File1**: Description of changes
- **File2**: Description of changes

## Documentation
- [x] Requirements updated
- [x] API documentation updated
- [x] Architecture decisions documented

## Testing
- [x] All tests passing (X/Y tests)
- [x] No regressions in existing functionality
- [x] New functionality covered by tests

## Related
- GitHub Issue #X
- RFC document reference
- Related PR #Y

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

### CI Monitoring

**⚠️ CRITICAL: CI MUST PASS BEFORE COMPLETION**

CI passing is MANDATORY. A feature is NOT complete until:
- All smoke tests pass
- All unit tests pass  
- No test failures
- No linting errors
- All CI checks show green

**Monitor CI status in PR:**

```bash
# Check PR status
gh pr view
gh pr checks

# Watch for CI results
gh run list --branch feature/your-feature-name

# Check specific test results locally
poetry run pytest tests/ -xvs
```

### Common CI Issues and Prevention

**Issue: Tools not appearing in agent context**
- **Symptom:** Tools defined in tools.json but not in agent input (%%% tools:category missing)
- **Root cause:** Forgot to add new category to category_order in tools/loader.py
- **Prevention:** Always check category_order when adding new tool categories
- **Verification:** Run `get_aui_tools()` and check for new category section

**Issue: Test failures due to hardcoded counts**
- **Symptom:** Tests fail with AssertionError on tool counts after adding tools
- **Root cause:** Tests use hardcoded numbers instead of schema-based validation
- **Prevention:** Use `assert len(registered) == len(get_tool_names())` not hardcoded numbers
- **Verification:** Run tests before committing, check for hardcoded patterns

**Issue: Global agent tests failing**
- **Symptom:** Tests checking _global_agent directly fail with None or AttributeError
- **Root cause:** Tests check internal implementation details instead of public API
- **Prevention:** Use get_global_agent() public API, not _global_agent variable
- **Verification:** Ensure tests trigger lazy initialization properly

### CI Failure Handling

**Simple Fixes** (apply directly):
- Typos in workflow files
- Missing test files
- Simple test assertion updates
- Configuration issues
- Category order updates

**Complex Issues** (escalate to human):
- Architecture-level failures
- Test design issues
- Environment/setup problems
- Intermittent failures

**Decision Tree:**
```
CI Failure?
│
├─ Can I clearly identify the root cause?
│  ├─ Yes ── Is it a simple, obvious fix?
│  │        ├─ Yes ── Apply fix, push to branch
│  │        │         ├─ Re-run tests locally
│  │        │         ├─ Verify fix works
│  │        │         └─ Push to remote
│  │        └─ No  ── Escalate to human
│  └─ No  ── Escalate to human
```

**Pre-commit Testing:**
Before pushing, always run:
```bash
# Run full test suite
poetry run pytest tests/ -x

# Check for common issues
poetry run python3 -c "from tools.loader import get_aui_tools; assert '%%% tools:<new_category>' in get_aui_tools()"
```

## Phase 5: Completion & Alert

### ⚠️ CRITICAL: CI MUST PASS BEFORE COMPLETION

**A feature is NOT complete until ALL CI checks pass.**

Do NOT alert the human for review until:
- ✅ All smoke tests pass
- ✅ All unit tests pass
- ✅ Zero test failures
- ✅ Zero linting errors
- ✅ All CI checks show green

**If CI is failing:**
1. Identify root cause using CI failure handling guidance (Phase 4)
2. Apply simple fixes immediately
3. Escalate complex issues to human
4. Re-run tests and verify fixes locally
5. Push updates and wait for CI to pass
6. ONLY then proceed to alert human

### PR Readiness Checklist

Before alerting the human, verify:

- [ ] **✅ All CI checks pass** (MANDATORY - NO EXCEPTIONS)
  - Fast tests (smoke + unit) pass
  - No test failures
  - No linting errors
  - All CI checks show green

- [ ] **Tool registration verified** (Agent projects)
  - All new tools appear in agent context (check AUI output)
  - New categories added to category_order
  - Handlers properly registered and tested

- [ ] **Tests use schema-based validation** (NOT hardcoded)
  - Tests use `get_tool_names()` instead of hardcoded counts
  - Tests adapt to schema changes automatically
  - No brittle hardcoded numbers in tests

- [ ] **Code review ready**
  - Code follows project patterns
  - No obvious bugs or issues
  - Documentation is accurate

- [ ] **Tests are comprehensive**
  - New functionality tested
  - No regressions
  - Tests are well-organized

- [ ] **Documentation is current**
  - Requirements reflect implementation
  - API docs updated
  - Architecture decisions documented

### Alert Human

When PR is ready for review:

```bash
# Get PR URL
gh pr view --json url --jq .url

# Alert with status
```

**Alert Template:**
```
PR ready for review: {PR_URL}

✅ All checks passing
✅ Documentation updated
✅ Tests comprehensive
✅ No regressions

Changes: {brief description}
```

## Common Pitfalls

### Documentation Issues

**❌ Forgetting to update docs**
- Fix: Review documentation checklist before PR

**❌ Documentation drift from implementation**
- Fix: Verify requirements match actual code

**❌ Missing architecture decisions**
- Fix: Document why, not just what

### Test Issues

**❌ Tests check implementation, not behavior**
- Fix: Test outcomes, not internal details

**❌ Brittle tests that break easily**
- Fix: Use contracts and interfaces

**❌ Missing edge cases**
- Fix: Review test coverage for completeness

### CI Issues

**❌ Hardcoded test expectations**
- Fix: Update test expectations when adding features
- Example: Adding new tools requires updating tool count tests

**❌ Environment differences**
- Fix: Ensure CI environment matches local setup

**❌ Timeout issues**
- Fix: Adjust test timeouts or optimize slow tests

**❌ Shared test state causing flaky tests**
- Fix: Prefer test isolation over shared fixtures
- **Pattern**: "Shared instance = faster but complex tests. Isolated instances = slower but simple, reliable tests."
- **When to isolate**: If tests require wait loops, cleanup logic, or timing dependencies → isolate them
- **Benefit**: Simpler, more reliable tests that are easier to understand and maintain
- **Cost**: Accept performance overhead for test reliability (10 tests × 10s startup = 100s total vs complex timing issues)

### Tool Registration Issues (Agent-Based Projects)

**❌ "Handler not implemented for: <tool_name>" error**
- Fix: Add handler function to agent imports in agent.py
- Example: Missing `handle_web_search` in `from tools.handlers import ...`
- **Verification**: Check `_TOOL_HANDLERS` mapping includes your tool

**❌ Handler exists but not registered**
- Fix: Add handler to explicit imports in agent.py
- Even if handler exists in handlers.py, agent needs explicit import
- Pattern: `from tools.handlers import handle_<tool_name>`

**❌ Smoke tests pass but runtime fails**
- Reason: Smoke tests don't verify handler registration
- Fix: Run unit tests like `test_execute_tool_call_registered_tools`
- Manual check: `python -c "from mars.agent import _TOOL_HANDLERS; print(_TOOL_HANDLERS.keys())"`

## Related Skills

- **continue-implementation**: Detailed implementation execution
- **memogarden-development**: Development environment and patterns
- **memogarden-testing**: Testing philosophy and practices
- **memogarden-debugging**: Debugging workflows
- **change-commit**: Git commit workflow

## Quick Reference

**Start new feature:**
1. Create GitHub issue
2. `git checkout -b feature/name`
3. Follow continue-implementation
4. Review docs and tests
5. `git push && gh pr create`
6. Monitor CI, fix simple issues
7. Alert human when ready

**Check PR status:**
```bash
gh pr view
gh pr checks
gh run list
```

**Monitor CI:**
```bash
# Watch for new runs
gh run list --branch feature/your-name --limit 1

# Check specific run
gh run view <run-id>
```

---

**Last Updated:** 2026-06-02
**For:** AI agents working on feature development
**Maintained by:** Project contributors
