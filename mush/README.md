# MUSH - Multi-User Session Host

**MUSH** is a PTY daemon enabling safe human–agent joint operation over existing CLI programs.

Traditional terminals are hostile to agents due to blocking input, implicit state, and byte-stream I/O. MUSH provides explicit control arbitration, inferred input intent, and complete auditability.

## Status

🚧 **Alpha Development** — M0 milestone in progress (Engine Binding)

## Documentation

- [Implementation Plan](docs/mush-implementation-plan.md)
- [PRD v0.2](docs/mush-prd-v0_2.md)
- [Architecture v0.1](docs/mush-architecture-v0_1.md)
- [Python API v0.1](docs/mush-python-api-v0_1.md)

## Quick Start (Development)

```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Build libghostty-vt (M0 milestone)
cd vendor/libghostty-vt
zig build -Demit-lib-vt
```

## Architecture

MUSH has two layers:

- **Layer 1** (`mush._ghostty`): cffi binding to libghostty-vt C engine
- **Layer 2** (`mush.vt`): Python API for terminal state

The daemon layer (`mush.daemon`) implements PTY hosting, control arbitration, and event logging.

Frontends:
- Stage 1: TUI client over Unix socket
- Stage 2: Browser with ghostty-web
- Agent: MCP server

## License

MIT
