#!/bin/bash
set -e

# setup-spacetimedb.sh
# Checks for SpacetimeDB and handles installation/initialization.

echo "--- SpacetimeDB Setup ---"

# 1. Detect OS
OS_TYPE=$(uname -s)
echo "Detected OS: $OS_TYPE"

# 2. Preferred Package Manager (e.g., bun)
# We store this in ~/.bond/config.json for persistence
BOND_CONFIG="$HOME/.bond/config.json"
mkdir -p "$HOME/.bond"
if [ ! -f "$BOND_CONFIG" ]; then
    echo "{\"package_manager\": \"bun\"}" > "$BOND_CONFIG"
fi
PACKAGE_MANAGER=$(grep -oP '"package_manager":\s*"\K[^"]+' "$BOND_CONFIG" || echo "bun")
echo "Using package manager: $PACKAGE_MANAGER"

# 2. Check for SpacetimeDB CLI
if ! command -v spacetime &> /dev/null; then
    echo "SpacetimeDB CLI not found."
    read -p "Would you like to install SpacetimeDB CLI now? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Installing SpacetimeDB CLI..."
        if [ "$OS_TYPE" == "Linux" ] || [ "$OS_TYPE" == "Darwin" ]; then
            # Command for Linux and macOS
            curl -sSf https://install.spacetimedb.com | sh
            export PATH="$HOME/.spacetime/bin:$PATH"
        elif [[ "$OS_TYPE" == *"NT"* ]] || [[ "$OS_TYPE" == *"MINGW"* ]]; then
            # Command for Windows (PowerShell)
            powershell.exe -Command "iwr https://windows.spacetimedb.com -useb | iex"
            # Note: Path update on Windows might require a new shell, but we try to continue
        else
            echo "Automatic installation not supported for $OS_TYPE. Please visit https://spacetimedb.com/install"
            exit 1
        fi
    else
        echo "SpacetimeDB is required for the new Bond architecture. Setup aborted."
        exit 1
    fi
else
    echo "SpacetimeDB CLI is already installed."
fi

# 3. SpacetimeDB Instance
# Resolve target URL from env override → bond.json (no fallback — fail loud
# if neither is set so we never silently target the wrong host). Must run
# before module init because `spacetime login` in CLI 2.0.5+ requires a
# reachable server (the old `--anonymous` flag was removed).
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -z "${SPACETIMEDB_URL:-}" ]; then
    SPACETIMEDB_URL=$(python3 -c "import json,sys; v=json.load(open('$PROJECT_ROOT/bond.json')).get('spacetimedb', {}).get('url'); sys.exit(1) if not v else print(v)" 2>/dev/null) || {
        echo "ERROR: SpacetimeDB URL not configured." >&2
        echo "  Set \$SPACETIMEDB_URL or add spacetimedb.url to $PROJECT_ROOT/bond.json" >&2
        exit 1
    }
fi
SPACETIME_HOST=$(python3 -c "from urllib.parse import urlparse; print(urlparse('$SPACETIMEDB_URL').hostname or 'localhost')")
SPACETIME_PORT=$(python3 -c "from urllib.parse import urlparse; u=urlparse('$SPACETIMEDB_URL'); print(u.port or 18787)")
echo "Target SpacetimeDB: $SPACETIMEDB_URL"

if ! curl -s "$SPACETIMEDB_URL/v1/health" &> /dev/null; then
    echo "SpacetimeDB instance not detected at $SPACETIMEDB_URL."
    # Only offer to start a local Docker container when the configured host
    # is local — for remote servers (loki, etc.) the container is managed
    # elsewhere and we should not try to start one here.
    if [[ "$SPACETIME_HOST" == "localhost" || "$SPACETIME_HOST" == "127.0.0.1" || "$SPACETIME_HOST" == "0.0.0.0" ]]; then
        read -p "Would you like to start a local SpacetimeDB instance via Docker? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            echo "Starting SpacetimeDB via Docker on port $SPACETIME_PORT with persistent volume..."
            mkdir -p "$HOME/.bond/spacetimedb"
            docker run -d \
                --name bond-spacetimedb \
                --pull always \
                -p $SPACETIME_PORT:3000 \
                -v "$HOME/.bond/spacetimedb:/var/lib/spacetimedb" \
                clockworklabs/spacetime:latest \
                start
            # Wait for server to become healthy before proceeding to login
            for i in {1..30}; do
                curl -s "$SPACETIMEDB_URL/v1/health" &> /dev/null && break
                sleep 1
            done
        else
            echo "Please ensure a SpacetimeDB instance is running at $SPACETIMEDB_URL before starting Bond."
        fi
    else
        echo "Remote host detected ($SPACETIME_HOST). Start the container on that server — not here."
        echo "Setup cannot continue without a reachable SpacetimeDB."
        exit 1
    fi
else
    echo "SpacetimeDB instance is reachable at $SPACETIMEDB_URL."
fi

# 4. Initialize SpacetimeDB Module (TypeScript)
# The module lives at ./spacetimedb/spacetimedb/ (nested — outer dir is the
# workspace, inner dir has package.json and src/). If either of the two
# config files is missing, regenerate just the missing ones.
#
# In CLI 2.1.0 `spacetime init` refuses to run against a non-empty dir and
# there's no --force. So we scaffold into a throwaway temp dir and copy
# only the files we need, leaving the rest of the workspace (AGENTS.md,
# frontend/, gateway/, spacetimedb/src/, spacetime.local.json, etc.)
# untouched.
MODULE_DIR="./spacetimedb"
MODULE_PKG="$MODULE_DIR/spacetimedb/package.json"
MODULE_WORKSPACE_JSON="$MODULE_DIR/spacetime.json"
if [ ! -f "$MODULE_PKG" ] || [ ! -f "$MODULE_WORKSPACE_JSON" ]; then
    echo "Initializing SpacetimeDB TypeScript module in $MODULE_DIR..."
    mkdir -p "$MODULE_DIR"

    # In SpacetimeDB CLI 2.0.5+, `--anonymous` was removed. Use
    # `--server-issued-login` against the configured server to mint a token
    # without going through spacetimedb.com OAuth.
    if ! spacetime list 2>/dev/null; then
        echo "No SpacetimeDB identity detected. Logging in against $SPACETIMEDB_URL..."
        spacetime login --server-issued-login "$SPACETIMEDB_URL"
    fi

    SCAFFOLD_DIR=$(mktemp -d)
    trap 'rm -rf "$SCAFFOLD_DIR"' EXIT

    echo "Scaffolding into $SCAFFOLD_DIR (you'll be prompted for project details)..."
    # Run interactively so the user answers database name / server / etc.
    # Default --project-path is ./<PROJECT_NAME>, so the scaffold lands at
    # $SCAFFOLD_DIR/bond-core.
    (cd "$SCAFFOLD_DIR" && spacetime init --lang typescript bond-core)

    if [ ! -f "$MODULE_WORKSPACE_JSON" ] && [ -f "$SCAFFOLD_DIR/bond-core/spacetime.json" ]; then
        cp "$SCAFFOLD_DIR/bond-core/spacetime.json" "$MODULE_WORKSPACE_JSON"
        echo "  Restored $MODULE_WORKSPACE_JSON"
    fi
    if [ ! -f "$MODULE_PKG" ] && [ -f "$SCAFFOLD_DIR/bond-core/spacetimedb/package.json" ]; then
        mkdir -p "$MODULE_DIR/spacetimedb"
        cp "$SCAFFOLD_DIR/bond-core/spacetimedb/package.json" "$MODULE_PKG"
        echo "  Restored $MODULE_PKG"
    fi
else
    echo "SpacetimeDB module already exists at $MODULE_DIR."
fi

# 5. Setup Backup Cron Job
echo "Setting up backup cron job..."
chmod +x ./scripts/backup-spacetimedb.sh
BACKUP_SCRIPT_PATH=$(realpath ./scripts/backup-spacetimedb.sh)

# Create a unique line for the crontab
CRON_ENTRY="0 2 * * * $BACKUP_SCRIPT_PATH >> $HOME/.bond/backups/spacetimedb/backup.log 2>&1"

# Check if the script path is already in the crontab
if ! crontab -l 2>/dev/null | grep -Fq "$BACKUP_SCRIPT_PATH"; then
    (crontab -l 2>/dev/null; echo "$CRON_ENTRY") | crontab -
    echo "Cron job added: Runs daily at 2:00 AM."
else
    # It exists, but let's ensure it's exactly what we want (idempotent update)
    (crontab -l 2>/dev/null | grep -Fv "$BACKUP_SCRIPT_PATH"; echo "$CRON_ENTRY") | crontab -
    echo "Cron job verified/updated."
fi

echo "--- SpacetimeDB Setup Complete ---"
