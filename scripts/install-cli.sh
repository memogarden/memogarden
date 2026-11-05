#!/usr/bin/env bash
# TENTATIVE (memogarden/memogarden#1): installation procedure not finalised;
# uv-tool default and symlink fallback both verified manually on one machine.
# Install the `memogarden` CLI (memogarden-client) onto PATH.
#
# Scope (deliberate, see RFC-004 §2.1 "Command surfaces"):
#   - installs the CLIENT surface: domain verbs (register provider, ...)
#   - does NOT install server-lifecycle verbs (serve/run/deploy/repair):
#     the server runs via systemd (scripts/install-user-service.sh), which
#     invokes the API package's venv directly. When lifecycle verbs become
#     an entry point, this shim becomes the verb router (bare serve/run/
#     deploy/repair -> server; verb+noun -> client).
#
# Methods:
#   uv      (default) `uv tool install --editable` — self-managed venv and
#           PATH shim under uv's control; survives project-venv churn.
#           Editable, so the CLI always matches the checkout (no versioning).
#   symlink ln -s from the client project venv into ~/.local/bin — lighter,
#           but couples the command to the dev venv lifecycle (dangling
#           link if the venv is recreated).
#
# Uninstall removes the PATH command only; the memogarden-client checkout
# and its dev venv are left untouched.
#
# Usage:
#   install-cli.sh install [--method uv|symlink] [--bin-dir DIR]
#   install-cli.sh uninstall
#   install-cli.sh status

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

COMMAND="${1:-install}"
shift || true

METHOD="uv"
BIN_DIR="$HOME/.local/bin"
CLIENT_DIR="$REPO_ROOT/memogarden-client"
CLIENT_VENV="$CLIENT_DIR/.venv"
ENTRY_POINT="$CLIENT_VENV/bin/memogarden"
PACKAGE_NAME="memogarden-client"

while [ $# -gt 0 ]; do
    case "$1" in
        --method) METHOD="$2"; shift 2 ;;
        --bin-dir) BIN_DIR="$2"; shift 2 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

warn_path() {
    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *) echo "warning: $BIN_DIR is not on PATH — add it to use 'memogarden'" >&2 ;;
    esac
}

uv_installed() {
    command -v uv >/dev/null 2>&1 && uv tool list 2>/dev/null | grep -q "^$PACKAGE_NAME"
}

do_install() {
    mkdir -p "$BIN_DIR"

    if [ "$METHOD" = "uv" ]; then
        if ! command -v uv >/dev/null 2>&1; then
            echo "error: uv not found (install uv, or use --method symlink)" >&2
            exit 1
        fi
        if uv_installed; then
            echo "already installed via uv (editable — always current with the checkout)"
            warn_path
            return 0
        fi
        uv tool install --editable "$CLIENT_DIR"
        echo "installed: $(command -v memogarden || echo "$BIN_DIR/memogarden")"
    elif [ "$METHOD" = "symlink" ]; then
        if [ ! -x "$ENTRY_POINT" ]; then
            echo "entry point missing — running 'poetry install' in memogarden-client..."
            (cd "$CLIENT_DIR" && poetry install)
        fi
        ln -sfn "$ENTRY_POINT" "$BIN_DIR/memogarden"
        echo "installed: $BIN_DIR/memogarden -> $ENTRY_POINT"
    else
        echo "error: unknown method '$METHOD' (expected uv or symlink)" >&2
        exit 2
    fi
    warn_path
}

do_uninstall() {
    local removed=0

    if uv_installed; then
        uv tool uninstall "$PACKAGE_NAME"
        removed=1
    fi

    # Remove a symlink shim only if it is ours (points into this checkout)
    if [ -L "$BIN_DIR/memogarden" ]; then
        local target
        target="$(readlink "$BIN_DIR/memogarden")"
        case "$target" in
            "$CLIENT_DIR"/*|*/memogarden-client/*)
                rm "$BIN_DIR/memogarden"
                echo "removed symlink $BIN_DIR/memogarden"
                removed=1
                ;;
            *) echo "keeping $BIN_DIR/memogarden (not ours: $target)" ;;
        esac
    fi

    [ "$removed" = "1" ] || echo "nothing to uninstall (no uv tool install, no symlink shim found)"
}

do_status() {
    if command -v memogarden >/dev/null 2>&1; then
        echo "command:  $(command -v memogarden)"
    else
        echo "command:  not on PATH"
    fi

    if [ -L "$BIN_DIR/memogarden" ]; then
        local target
        target="$(readlink "$BIN_DIR/memogarden")"
        if [ -e "$BIN_DIR/memogarden" ]; then
            echo "symlink:  $BIN_DIR/memogarden -> $target (ok)"
        else
            echo "symlink:  $BIN_DIR/memogarden -> $target (DANGLING)"
        fi
    fi

    if uv_installed; then
        echo "uv tool:  $PACKAGE_NAME (editable)"
    else
        echo "uv tool:  not installed"
    fi

    echo "surface:  client domain verbs only; server lifecycle verbs (serve/run/deploy/repair) are not installed (systemd owns the server — scripts/install-user-service.sh)"
}

case "$COMMAND" in
    install)   do_install ;;
    uninstall) do_uninstall ;;
    status)    do_status ;;
    *) echo "usage: install-cli.sh {install|uninstall|status} [--method uv|symlink] [--bin-dir DIR]" >&2; exit 2 ;;
esac
