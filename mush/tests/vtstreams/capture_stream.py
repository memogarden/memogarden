#!/usr/bin/env python3
"""
VT stream capture script for MUSH testing.

This script captures real PTY byte streams from terminal sessions for use
as test fixtures. These fixtures are critical for M0 exit gate verification.
"""

import os
import pty
import subprocess
import sys
import select
import tempfile
from pathlib import Path

# Fixture definitions
FIXTURES = {
    "bash_prompt": {
        "command": ["bash", "-i", "-c", "echo 'test'; sleep 0.1"],
        "description": "Bash shell prompt",
    },
    "vim_session": {
        "command": ["vim", "-c", "itest", "-c", "quit"],
        "description": "Vim editor session",
    },
    "htop_session": {
        "command": ["htop"],
        "description": "htop (requires manual quit)",
    },
    "ls_color": {
        "command": ["ls", "--color=always"],
        "description": "ls with color output",
    },
    "sudo_password": {
        "command": ["sudo", "-k", "true"],
        "description": "sudo with password prompt",
    },
    "pathological": {
        "command": ["bash", "-c", "for i in {1..100}; do echo -ne \"\\r[$i/100]\"; sleep 0.01; done"],
        "description": "High-frequency redraws",
    },
}


def capture_stream(name, command, timeout=5):
    """Capture PTY stream from a command.

    Args:
        name: Fixture name
        command: Command list to execute
        timeout: Timeout in seconds

    Returns:
        bytes: Captured PTY output
    """
    print(f"Capturing {name}...")
    print(f"Command: {' '.join(command)}")

    # Create PTY
    master_fd, slave_fd = pty.openpty()

    try:
        # Start process in PTY
        proc = subprocess.Popen(
            command,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
        )

        # Close slave_fd in parent
        os.close(slave_fd)

        output = bytearray()
        start_time = __import__("time").time()

        while True:
            # Check timeout
            if __import__("time").time() - start_time > timeout:
                print(f"  Timeout after {timeout}s")
                break

            # Check if process is still running
            if proc.poll() is not None:
                # Process exited, give it a moment to flush
                __import__("time").sleep(0.1)
                # Read remaining data
                try:
                    while True:
                        data = os.read(master_fd, 4096)
                        if not data:
                            break
                        output.extend(data)
                except OSError:
                    pass
                break

            # Wait for data to be available
            rlist, _, _ = select.select([master_fd], [], [], 0.1)

            if master_fd in rlist:
                try:
                    data = os.read(master_fd, 4096)
                    if data:
                        output.extend(data)
                except OSError:
                    break

        print(f"  Captured {len(output)} bytes")
        return bytes(output)

    finally:
        # Cleanup
        os.close(master_fd)
        try:
            proc.terminate()
            proc.wait(timeout=1)
        except:
            proc.kill()


def save_fixture(name, data):
    """Save captured stream to fixture file.

    Args:
        name: Fixture name
        data: Captured bytes
    """
    fixtures_dir = Path(__file__).parent
    stream_file = fixtures_dir / f"{name}.stream"

    # Save raw stream
    stream_file.write_bytes(data)

    # TODO: Generate golden outputs using reference terminal
    print(f"  Saved to {stream_file}")
    print(f"  WARNING: Golden outputs not yet generated (requires reference terminal)")


def main():
    if len(sys.argv) < 2:
        print("Usage: capture_stream.py <fixture_name>")
        print()
        print("Available fixtures:")
        for name, info in FIXTURES.items():
            print(f"  {name:20s} - {info['description']}")
        sys.exit(1)

    name = sys.argv[1]

    if name not in FIXTURES:
        print(f"Unknown fixture: {name}")
        print(f"Available fixtures: {', '.join(FIXTURES.keys())}")
        sys.exit(1)

    fixture_info = FIXTURES[name]
    command = fixture_info["command"]

    # Capture the stream
    data = capture_stream(name, command, timeout=5)

    # Save to file
    save_fixture(name, data)

    print(f"\nFixture {name} captured successfully!")
    print(f"Next steps:")
    print(f"  1. Manually verify the output if needed")
    print(f"  2. Generate golden outputs (requires reference terminal)")
    print(f"  3. Test with: pytest tests/fixtures/test_roundtrip.py -k {name}")


if __name__ == "__main__":
    main()
