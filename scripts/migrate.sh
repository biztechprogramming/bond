#!/bin/bash
# Run database migrations using golang-migrate
# Requires: migrate CLI with SQLite support
# Install: make install-migrate

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
MIGRATIONS_PATH="$PROJECT_ROOT/migrations"
BOND_HOME="${BOND_HOME:-$HOME/.bond}"
DATA_DIR="$BOND_HOME/data"
DB_FILE="$DATA_DIR/knowledge.db"

# Ensure data directory exists
mkdir -p "$DATA_DIR"

# Use Go-installed migrate (has SQLite support) if available
if [ -x "$HOME/go/bin/migrate" ]; then
    MIGRATE="$HOME/go/bin/migrate"
elif command -v migrate &> /dev/null; then
    MIGRATE="migrate"
else
    echo "Error: 'migrate' CLI not found."
    echo ""
    echo "Install with SQLite support:"
    echo "  make install-migrate"
    exit 1
fi

TARGET_VERSION="$1"
DB_URL="sqlite3://$DB_FILE"

echo "Running migrations..."
echo "  Using: $MIGRATE"
echo "  Path: $MIGRATIONS_PATH"
echo "  Database: $DB_FILE"

if [ -n "$TARGET_VERSION" ]; then
    # Validate target is a positive integer
    if ! echo "$TARGET_VERSION" | grep -qE '^[0-9]+$'; then
        echo "Error: Target version must be a positive integer, got '$TARGET_VERSION'"
        exit 1
    fi

    # Get current version (returns "X" or "X (dirty)")
    CURRENT_OUTPUT=$($MIGRATE -path "$MIGRATIONS_PATH" -database "$DB_URL" version 2>&1 || true)
    CURRENT_VERSION=$(echo "$CURRENT_OUTPUT" | grep -oE '^[0-9]+' || echo "")

    if [ -z "$CURRENT_VERSION" ]; then
        echo "Error: Could not determine current migration version."
        echo "  Output: $CURRENT_OUTPUT"
        echo "  If no migrations have been applied yet, run 'make migrate' first."
        exit 1
    fi

    MIN_ALLOWED=$((CURRENT_VERSION - 3))
    MAX_ALLOWED=$((CURRENT_VERSION + 2))
    [ "$MIN_ALLOWED" -lt 1 ] && MIN_ALLOWED=1

    if [ "$TARGET_VERSION" -lt "$MIN_ALLOWED" ] || [ "$TARGET_VERSION" -gt "$MAX_ALLOWED" ]; then
        echo "Error: Target version $TARGET_VERSION is out of safe range."
        echo "  Current version: $CURRENT_VERSION"
        echo "  Allowed range:   $MIN_ALLOWED .. $MAX_ALLOWED (current -3 to current +2)"
        exit 1
    fi

    echo "  Current version: $CURRENT_VERSION"
    echo "  Forcing to version: $TARGET_VERSION"
    $MIGRATE -path "$MIGRATIONS_PATH" -database "$DB_URL" force "$TARGET_VERSION"
else
    echo "  Skipping version check — running all pending migrations up to latest. $0"
    # Normal: run all pending up migrations
    $MIGRATE -path "$MIGRATIONS_PATH" -database "$DB_URL" up
fi

echo "SQLite migrations complete."

# Run SpacetimeDB migrations (publish module)
SPACETIMEDB_URL="${SPACETIMEDB_URL:-$(python3 -c "import json; print(json.load(open('$PROJECT_ROOT/bond.json')).get('spacetimedb', {}).get('url', 'http://localhost:18787'))" 2>/dev/null || echo "http://localhost:18787")}"

# Load token from .env and write directly to spacetime CLI config
if [ -f "$PROJECT_ROOT/.env" ]; then
    SPACETIMEDB_TOKEN=$(grep -oP '^SPACETIMEDB_TOKEN\s*=\s*"?\K[^"]+' "$PROJECT_ROOT/.env" 2>/dev/null || true)
    if [ -n "$SPACETIMEDB_TOKEN" ]; then
        SPACETIME_CONFIG_DIR="${HOME}/.config/spacetime"
        mkdir -p "$SPACETIME_CONFIG_DIR"
        echo "spacetimedb_token = \"$SPACETIMEDB_TOKEN\"" > "$SPACETIME_CONFIG_DIR/cli.toml"
        echo "  Token written to $SPACETIME_CONFIG_DIR/cli.toml"
    fi
fi

SPACETIMEDB_MODULE="$PROJECT_ROOT/spacetimedb/spacetimedb"
SPACETIMEDB_CONFIG_DIR="$PROJECT_ROOT/spacetimedb"
SPACETIMEDB_DATABASE=$(python3 -c "import json; print(json.load(open('$SPACETIMEDB_CONFIG_DIR/spacetime.local.json')).get('database', json.load(open('$SPACETIMEDB_CONFIG_DIR/spacetime.json')).get('database', '')))" 2>/dev/null)

spacetime_publish() {
    local server_url="$1"
    local output
    local exit_code

    set +e
    output=$(spacetime publish --server "$server_url" --yes $SPACETIMEDB_DATABASE 2>&1)
    exit_code=$?
    set -e

    echo "$output"

    if [ $exit_code -ne 0 ] && echo "$output" | grep -qiE "401|Unauthorized|InvalidSignature|InvalidToken"; then
        echo "  Auth error detected — fetching fresh token from $server_url..."
        local fresh_token
        fresh_token=$(curl -s -X POST "$server_url/v1/identity" 2>/dev/null | \
            python3 -c "import sys,json; print(json.load(sys.stdin)['token'])" 2>/dev/null)

        if [ -z "$fresh_token" ]; then
            echo "  Failed to fetch token from $server_url — is the server running?"
            return $exit_code
        fi

        # Write fresh token directly to CLI config
        SPACETIME_CONFIG_DIR="${HOME}/.config/spacetime"
        mkdir -p "$SPACETIME_CONFIG_DIR"
        echo "spacetimedb_token = \"$fresh_token\"" > "$SPACETIME_CONFIG_DIR/cli.toml"

        echo "  Retrying publish..."
        spacetime publish --server "$server_url" --yes $SPACETIMEDB_DATABASE
        return $?
    fi

    return $exit_code
}

if curl -s "$SPACETIMEDB_URL/v1/health" > /dev/null 2>&1; then
    echo ""
    echo "Publishing SpacetimeDB module..."
    echo "  Module: $SPACETIMEDB_MODULE"
    echo "  Server: $SPACETIMEDB_URL"

    cd "$SPACETIMEDB_MODULE"
    spacetime_publish "$SPACETIMEDB_URL"
    echo "SpacetimeDB migrations complete."

    echo ""
    echo "Regenerating SpacetimeDB TypeScript bindings..."
    spacetime generate --lang typescript --out-dir "$PROJECT_ROOT/spacetimedb/frontend/src/lib/spacetimedb" --module-path "$SPACETIMEDB_MODULE"
    spacetime generate --lang typescript --out-dir "$PROJECT_ROOT/spacetimedb/gateway/src/spacetimedb" --module-path "$SPACETIMEDB_MODULE"
    echo "TypeScript bindings regenerated."
else
    echo ""
    echo "SpacetimeDB not running at $SPACETIMEDB_URL — skipping module publish."
    echo "  Start it with: spacetime start --listen-addr 127.0.0.1:18787 --data-dir ~/.bond/spacetimedb"
fi
