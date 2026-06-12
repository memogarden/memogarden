# VT Stream Fixtures

This directory contains recorded PTY byte streams captured from real terminal sessions.

## Fixture Streams

Each fixture has:
- `<name>.stream` - Raw PTY bytes captured from a terminal session
- `<name>.golden.txt` - Expected plain-text output
- `<name>.golden.vt` - Expected VT re-emission output

## Required Fixtures (M0 Exit Gate)

1. **bash_prompt** - Bash shell prompt
2. **vim_session** - Vim editor (enter, edit, quit)
3. **htop_session** - htop (open, resize, quit)
4. **ls_color** - `ls --color` command
5. **sudo_password** - sudo command with password prompt (SECRET mode)
6. **apt_install** - apt install progress bar
7. **pathological** - High-frequency redraws + raw input

## Capturing Fixtures

To capture new fixtures, use the `capture_stream.py` script:

```bash
python tests/vtstreams/capture_stream.py bash_prompt
python tests/vtstreams/capture_stream.py vim_session
# ... etc
```

The script will:
1. Run the appropriate program in a PTY
2. Capture all bytes written to the PTY master
3. Save to `<name>.stream`
4. Generate golden outputs using a reference terminal emulator

## Manual Capture Procedure

If auto-capture fails, manually capture using:

```bash
# Using script command
script -c "bash -i" /tmp/bash_prompt.typescript
# Then extract the raw bytes and save to .stream file

# Or using expect
expect capture_bash_prompt.exp
```

## Verification

Test the fixtures:

```bash
pytest tests/fixtures/test_roundtrip.py -v
```

This verifies that:
- Terminal round-trips all fixtures (text matches golden output)
- VT re-emission can be re-ingested
- Device-query fixtures trigger on_write_pty callbacks
