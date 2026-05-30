---
name: claude-code-reference
description: Reference for Claude Code features, hooks, slash commands, and capabilities. Use when working with Claude Code configuration, customizing agent behavior, or answering questions about how Claude Code works.
---

# Claude Code Reference

This skill provides quick access to Claude Code documentation when you need to understand or customize how Claude Code works.

## When to Use This Skill

Use this skill when:
- Setting up or configuring Claude Code
- Creating hooks or slash commands
- Understanding agent capabilities and limitations
- Debugging Claude Code behavior
- Learning about Claude Code features (MCP servers, IDE integrations, etc.)

## Quick Reference

### Essential Concepts

**Hooks**: Shell commands that run in response to events (before/after tool calls)
- Location: `.claude/hooks/` in your project
- Documentation: https://code.claude.com/docs/en/hooks

**Slash Commands**: Custom user-invoked commands you can define
- Location: `.claude/commands/` in your project
- Documentation: https://code.claude.com/docs/en/commands

**Agent Skills**: Modular capabilities for domain-specific expertise
- Location: `~/.claude/skills/` (personal) or `.claude/skills/` (project)
- Documentation: https://code.claude.com/docs/en/skills

**MCP Servers**: Model Context Protocol servers for external integrations
- Location: Configured in settings
- Documentation: https://code.claude.com/docs/en/mcp

### Configuration Files

`.claude/config.json` - Project-specific Claude Code settings
`.claude/hooks/<event>` - Event-triggered shell commands
`.claude/commands/<name>.md` - Custom slash commands
`.claude/skills/<name>/SKILL.md` - Project-specific skills

## Accessing Full Documentation

### Method 1: Fetch llms.txt (Recommended)

The llms.txt file contains a comprehensive index of all Claude Code documentation:

```bash
curl -s https://code.claude.com/docs/llms.txt | less
```

This file provides links to all documentation sections.

### Method 2: Web Fetch Specific Pages

For specific topics, fetch the relevant documentation page:

```bash
# Hooks documentation
curl -s https://code.claude.com/docs/en/hooks

# Commands documentation
curl -s https://code.claude.com/docs/en/commands

# Skills documentation
curl -s https://code.claude.com/docs/en/skills

# MCP documentation
curl -s https://code.claude.com/docs/en/mcp
```

### Method 3: Browse Online

Visit https://code.claude.com/docs/ for the full documentation browser.

## Common Tasks

### Creating a Hook

Hooks run shell commands before/after tool executions. Example in `.claude/hooks/after-Bash.md`:

```markdown
#!/bin/bash
# Run tests after writing Python files
if [[ "$1" == *.py ]]; then
  poetry run pytest
fi
```

### Creating a Slash Command

Commands are user-invoked shortcuts. Example in `.claude/commands/test.md`:

```markdown
---
description: Run the test suite
---

Run all tests: `poetry run pytest`

Run with coverage: `poetry run pytest --cov=memogarden_core`
```

### Debugging Hook Issues

1. Check hook file has execute permissions: `chmod +x .claude/hooks/*`
2. Test hook manually: Run the shell command directly
3. Check hook output: Hooks print to stdout/stderr
4. Verify file path: Hooks must be in `.claude/hooks/` with correct event name

## Claude Code vs. Other Claude Products

- **Claude Code**: CLI tool for coding tasks (filesystem-based skills, local execution)
- **Claude API**: Programmatic access (uploadable skills, no network access)
- **Claude.ai**: Web interface (uploadable skills, individual user scope)

This skill is specifically for Claude Code (the CLI tool).

## Project Context vs. This Skill

- **AGENTS.md**: Project-specific guidance for MemoGarden (always load)
- **This skill**: Claude Code reference material (load when needed)

When working on MemoGarden, you should always follow AGENTS.md conventions. Use this skill only when you need to understand or customize Claude Code itself.

## Key Documentation Sections

Based on llms.txt, the most relevant sections for agents are:

1. **Quickstart**: https://code.claude.com/docs/en/quickstart
2. **Hooks**: https://code.claude.com/docs/en/hooks
3. **Commands**: https://code.claude.com/docs/en/commands
4. **Skills**: https://code.claude.com/docs/en/skills
5. **MCP**: https://code.claude.com/docs/en/mcp
6. **Settings**: https://code.claude.com/docs/en/settings

## Troubleshooting

**Skill not loading**:
- Check SKILL.md has valid YAML frontmatter
- Verify skill directory path: `~/.claude/skills/<name>/SKILL.md`
- Check name is lowercase with hyphens only

**Hook not firing**:
- Verify hook file name matches event exactly (e.g., `after-Bash.md`)
- Check hook is in `.claude/hooks/` directory
- Test hook manually to verify it works

**Command not available**:
- Check command is in `.claude/commands/` directory
- Verify file has proper frontmatter with description
- Restart Claude Code after adding commands

---

**Note**: This skill provides reference material. For project-specific workflows, see AGENTS.md and project-specific skills (memogarden-testing, memogarden-api-endpoint, etc.).
