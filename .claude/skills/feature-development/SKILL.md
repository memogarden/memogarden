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

```
┌─────────────────────────────────────────────────────────┐
│ 1. ISSUE CREATION & DISCUSSION                                 │
├─────────────────────────────────────────────────────────┤
│ • Create GitHub issue describing problem                   │
│ • Discuss solution approach if needed                     │
│ • Get alignment on requirements before implementation      │
└─────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────┐
│ 2. BRANCH CREATION & IMPLEMENTATION                            │
├─────────────────────────────────────────────────────────┤
│ • Create feature branch from main                             │
│ • Follow continue-implementation skill for execution        │
│ • Make changes, write tests, update docs                     │
└─────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────┐
│ 3. QUALITY GATES                                                    │
├─────────────────────────────────────────────────────────┤
│ • Documentation review: accuracy and completeness          │
│ • Test coverage review: fidelity and organization          │
│ • Requirements review: reflect actual implementation       │
└─────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────┐
│ 4. CI INTEGRATION & MONITORING                                   │
├─────────────────────────────────────────────────────────┤
│ • Create PR to main branch                                     │
│ • Monitor CI status in PR                                     │
│ • Fix obvious CI issues; escalate complex ones              │
└─────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────┐
│ 5. COMPLETION & ALERT                                               │
├─────────────────────────────────────────────────────────┤
│ • Verify CI passes and PR is ready                             │
│ • Alert human for review and merge                             │
└─────────────────────────────────────────────────────────┘
```

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

**Monitor CI status in PR:**

```bash
# Check PR status
gh pr view
gh pr checks

# Watch for CI results
gh run list --branch feature/your-feature-name
```

### CI Failure Handling

**Simple Fixes** (apply directly):
- Typos in workflow files
- Missing test files
- Simple test assertion updates
- Configuration issues

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
│  │        └─ No  ── Escalate to human
│  └─ No  ── Escalate to human
```

## Phase 5: Completion & Alert

### PR Readiness Checklist

Before alerting the human, verify:

- [ ] **All CI checks pass**
  - Fast tests (smoke + unit) pass
  - No test failures
  - No linting errors

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

**Last Updated:** 2026-05-22
**For:** AI agents working on feature development
**Maintained by:** Project contributors
