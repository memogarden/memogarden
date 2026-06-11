---
name: gh
description: GitHub operations workflow covering both issues and pull requests. Use when creating issues, managing PRs, handling GitHub Actions, or any GitHub repository operations.
---

# GitHub Operations Workflow

This skill defines the standard workflow for GitHub operations including issues, pull requests, and repository management in MARS and related projects.

## When to Use This Skill

Use this skill when:
- Creating GitHub issues with consistent templates
- Creating pull requests from feature branches
- Managing GitHub repository operations
- Handling GitHub Actions workflow issues
- Following complete GitHub development workflow

## Key Lessons Learned

**Repository Context:**
- **Always specify `--repo` explicitly** - GitHub commands default to wrong repository
- Pattern: `gh issue create --repo memogarden/mars ...`
- Prevents accidental issue creation in wrong repos

**Consistent Templates:**
- Use standard issue templates for clarity
- Use standard PR description formats
- Cross-reference related issues and PRs

**Workflow Integration:**
- Issues → PRs → Merge pattern
- Link issues to PRs for traceability
- Include success criteria checklists

## Issue Workflow

### Create Issue

**Basic Issue Creation (no labels - most compatible):**
```bash
gh issue create --repo <repo> --title "Issue Title" --body "Description"
```

**Issue with Labels (two-step process):**
```bash
# Step 1: Create issue without labels
ISSUE_URL=$(gh issue create --repo <repo> --title "Issue Title" --body "Description")

# Step 2: Add labels (only if they exist)
gh issue edit <number> --repo <repo> --add-label "enhancement,documentation"
```

**Adding Labels After Creation:**
```bash
# Create issue first
gh issue create --repo <repo> --title "feat: add feature" --body "Description..."

# Then add labels to the created issue
gh issue edit <number> --repo <repo> --add-label "enhancement"

# Or add multiple labels
gh issue edit <number> --repo <repo> --add-label "enhancement,documentation"
```

**Issue Template:**
```markdown
## Problem Statement
[Describe the problem clearly and concisely]

## Current State
[What is the current situation? What are the limitations?]

## Proposed Solution
[Describe the proposed solution approach]

## Success Criteria
- [ ] Criterion 1
- [ ] Criterion 2
- [ ] Criterion 3

## Related
- Related issue/PR references
- Documentation links
- Context from previous work

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

**Issue Types:**
- `feat:` - New features
- `fix:` - Bug fixes
- `docs:` - Documentation improvements
- `refactor:` - Code refactoring
- `test:` - Test improvements
- `chore:` - Maintenance tasks

### Update Issue

```bash
# Update issue description
gh issue edit <number> --repo <repo> --body "New description"

# Add labels
gh issue edit <number> --repo <repo> --add-label "enhancement,documentation"

# Close issue
gh issue close <number> --repo <repo> --comment "Resolved by PR #X"
```

### View Issues

```bash
# List issues in repository
gh issue list --repo <repo>

# View specific issue
gh issue view <number> --repo <repo>

# List issues with specific labels
gh issue list --repo <repo> --label "enhancement"
```

### Issue Management Patterns

**Best Practice - Create First, Label After:**
```bash
# 1. Create issue without labels (most compatible)
gh issue create --repo memogarden/mars \
  --title "feat: add document versioning" \
  --body "Feature request template..."

# 2. Add labels if they exist (optional, after creation)
gh issue edit <number> --repo <repo> --add-label "enhancement"

# 3. Create feature branch
git checkout -b feature/document-versioning

# 4. Implement and create PR
gh pr create --repo memogarden/mars \
  --title "feat: add document versioning" \
  --body "Fixes #15"
```

**Why Two-Step Label Process?**
- Not all repositories have the same labels
- Creating issues without labels is more compatible
- Labels can be added after creation without errors
- Prevents workflow interruptions from missing labels

**Bug Report Pattern:**
```bash
# 1. Create issue for bug
gh issue create --repo memogarden/mars \
  --title "fix: memory leak in document processing" \
  --body "Bug report template..."

# 2. Create fix branch
git checkout -b fix/memory-leak-document-processing

# 3. Fix and create PR
gh pr create --repo memogarden/mars \
  --title "fix: memory leak in document processing" \
  --body "Fixes #16"
```

## Pull Request Workflow

### Standard PR Creation Flow

```
┌─────────────────────────────────────────────────────────┐
│ 1. CREATE FEATURE BRANCH                                      │
├─────────────────────────────────────────────────────────┤
│ • git checkout -b feature/your-feature-name                    │
│ • Make changes and commit                                           │
└─────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────┐
│ 2. PUSH FEATURE BRANCH TO REMOTE                             │
├─────────────────────────────────────────────────────────┤
│ • git push -u origin feature/your-feature-name                   │
└─────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────┐
│ 3. CREATE PULL REQUEST                                          │
├─────────────────────────────────────────────────────────┤
│ • gh pr create --repo <repo> --title "..." --body "..."              │
│ • Include comprehensive PR description                             │
│ • Link to related issues                                              │
└─────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────┐
│ 4. MERGE AND CLEANUP                                               │
├─────────────────────────────────────────────────────────┤
│ • gh pr merge <pr-number> --merge --delete-branch                   │
│ • git checkout main && git pull                                     │
│ • git branch -d feature/xxx                                           │
└─────────────────────────────────────────────────────────┘
```

### PR Creation Best Practices

**Branch Naming:**
```bash
# Good: Clear, descriptive
feature/document-management-system
feature/archived-documents-ux
fix/database-connection-leak

# Avoid: Vague or temporary
stuff
fix-branch
testing
```

**Commit Messages:**
```bash
# Format: <type>: <description>
feat: add document management system
fix: correct database migration error
docs: update API endpoint documentation
refactor: optimize database query performance
test: add integration tests for user flow
chore: update dependencies
```

**PR Description Template:**
```markdown
## Summary
One-line description of what this PR does and why.

## Problem Statement
What problem does this solve? What was the current limitation?

## Solution
How does this PR solve the problem? What approach was taken?

## Changes
- **File1**: Description of changes
- **File2**: Description of changes

## Testing
- [x] All tests passing (X/Y tests)
- [x] No regressions in existing functionality
- [x] New functionality covered by tests

## Related
- Fixes #X (issue number)
- Related to #Y
- Based on issue #Z

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

### Create PR Commands

```bash
# Basic PR creation
gh pr create --repo <repo> --title "Title" --body "Description"

# PR with issue reference
gh pr create --repo <repo> --title "fix: bug description" --body "Fixes #42"

# PR from specific branch
gh pr create --repo <repo> --base main --head feature-branch

# PR with draft status
gh pr create --repo <repo> --draft
```

### PR Management

```bash
# List PRs
gh pr list --repo <repo>

# View PR details
gh pr view <pr-number> --repo <repo>

# View PR checks
gh pr checks <pr-number> --repo <repo>

# Merge PR
gh pr merge <pr-number> --repo <repo> --merge --delete-branch

# Close PR without merging
gh pr close <pr-number> --repo <repo>
```

## GitHub Authentication & Tokens

### Required Scopes

**Basic Development:**
- `repo` - Full repository access (read/write)
- `gist` - Gist creation
- `read:org` - Organization reading (if applicable)

**GitHub Actions Workflows:**
- `workflow` - **Required** for creating/modifying `.github/workflows/` files

### Check Authentication

```bash
# Check current token and scopes
gh auth status

# Should show:
# Token scopes: 'gist', 'read:org', 'repo', 'workflow'
```

### Setup Workflow Token

```bash
# Login with workflow token
gh auth login --with-token

# Or set environment variable
export GH_TOKEN="gho_YOUR_WORKFLOW_TOKEN_HERE"

# Add to .env file
echo "GH_TOKEN=gho_YOUR_WORKFLOW_TOKEN_HERE" >> .env
```

## Common Issues & Solutions

### Issue: Wrong Repository Operations

**Problem:** Creating issues/PRs in wrong repository
```bash
# Wrong - uses default/repo context
gh issue create --title "title" --body "body"

# Correct - explicit repository
gh issue create --repo memogarden/mars --title "title" --body "body"
```

**Solution:** Always specify `--repo` explicitly

### Issue: Workflow Permission Errors

**Problem:** "refusing to allow an OAuth App to create or update workflow"

**Solution:**
```bash
# Check token scopes
gh auth status

# Re-authenticate with workflow token
gh auth login --with-token

# Verify workflow scope is present
```

### Issue: Branch Not Pushed

**Problem:** "you must first push the current branch to a remote"

**Solution:**
```bash
# Push branch first
git push -u origin feature/your-feature

# Then create PR
gh pr create --repo <repo> --base main
```

### Issue: Long Descriptions Truncated

**Problem:** PR/issue descriptions get cut off

**Solution:** Use file-based creation
```bash
# Write description to file
cat > description.md << 'EOF'
## Summary
Detailed description...
EOF

# Create with file
gh issue create --repo <repo> --body-file description.md
gh pr create --repo <repo> --body-file description.md
```

### Issue: Labels Don't Exist

**Problem:** "could not add label: 'label-name' not found" error

**Solution:** Create issue first, add labels after
```bash
# Step 1: Create without labels
gh issue create --repo <repo> --title "Title" --body "Body..."

# Step 2: Add labels separately (optional)
gh issue edit <number> --repo <repo> --add-label "enhancement"

# Or create label first if needed
gh label create enhancement --color "#0366d6"
```

**Why This Happens:**
- Different repositories have different label sets
- Labels may not exist in target repository
- Creating issues with labels requires all labels to exist
- Two-step process is more compatible across repositories

## Complete Workflow Example

### Feature Development Workflow

```bash
# 1. Create issue
gh issue create --repo memogarden/mars \
  --title "feat: add document versioning" \
  --body "Feature request template..."
# Returns: Issue #15 created

# 2. Create feature branch
git checkout main && git pull
git checkout -b feature/document-versioning

# 3. Implement feature
# ... make changes ...
git add .
git commit -m "feat: add document versioning"

# 4. Push and create PR
git push -u origin feature/document-versioning
gh pr create --repo memogarden/mars \
  --title "feat: add document versioning" \
  --body "Fixes #15" \
  --base main

# 5. Monitor and merge
gh pr checks <pr-number> --repo memogarden/mars
gh pr merge <pr-number> --repo memogarden/mars --merge --delete-branch

# 6. Cleanup
git checkout main && git pull
git branch -d feature/document-versioning
```

### Bug Fix Workflow

```bash
# 1. Report bug
gh issue create --repo memogarden/mars \
  --title "fix: memory leak in document processing" \
  --body "Bug report template..."
# Returns: Issue #16 created

# 2. Create fix branch
git checkout -b fix/memory-leak-document-processing

# 3. Implement fix
# ... make changes ...
git add .
git commit -m "fix: memory leak in document processing"

# 4. Push and create PR
git push -u origin fix/memory-leak-document-processing
gh pr create --repo memogarden/mars \
  --title "fix: memory leak in document processing" \
  --body "Fixes #16"

# 5. Merge and cleanup
gh pr merge <pr-number> --repo memogarden/mars --merge --delete-branch
git checkout main && git pull
git branch -d fix/memory-leak-document-processing
```

## Repository-Specific Notes

### MARS Repository
- **Main branch:** `main`
- **Feature branch pattern:** `feature/description`
- **Fix branch pattern:** `fix/description`
- **Required checks:** Smoke tests must pass

### Other Repositories
Check repository-specific documentation for:
- Main branch name (might be `develop`, `master`, etc.)
- Branch naming conventions
- Required checks for approval

## Quick Reference

**Issue Operations:**
```bash
gh issue create --repo <repo> --title "..." --body "..."
gh issue list --repo <repo>
gh issue view <number> --repo <repo>
gh issue close <number> --repo <repo>
```

**PR Operations:**
```bash
gh pr create --repo <repo> --title "..." --body "..."
gh pr list --repo <repo>
gh pr view <number> --repo <repo>
gh pr checks <number> --repo <repo>
gh pr merge <number> --repo <repo> --merge --delete-branch
```

**Authentication:**
```bash
gh auth status
gh auth login --with-token
```

---

**Last Updated:** 2026-05-22
**For:** AI agents working on MARS and related projects
**Maintained by:** Project contributors
