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

echo "Running migrations..."
echo "  Using: $MIGRATE"
echo "  Path: $MIGRATIONS_PATH"
echo "  Database: $DB_FILE"

# Run SQLite migrations
$MIGRATE -path "$MIGRATIONS_PATH" -database "sqlite3://$DB_FILE" up

echo "SQLite migrations complete."

# Run SpacetimeDB migrations (publish module)
SPACETIMEDB_URL="${SPACETIMEDB_URL:-http://localhost:18787}"
SPACETIMEDB_MODULE="$PROJECT_ROOT/spacetimedb/spacetimedb"
SPACETIMEDB_CONFIG_DIR="$PROJECT_ROOT/spacetimedb"
SPACETIMEDB_DATABASE=$(python3 -c "import json; print(json.load(open('$SPACETIMEDB_CONFIG_DIR/spacetime.local.json')).get('database', json.load(open('$SPACETIMEDB_CONFIG_DIR/spacetime.json')).get('database', '')))" 2>/dev/null)

spacetime_publish() {
    local server_url="$1"
    local output
    local exit_code

    set +e
    output=$(spacetime publish --server "$server_url" --yes --delete-data=on-conflict $SPACETIMEDB_DATABASE 2>&1)
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

        spacetime login --token "$fresh_token"

        echo "  Retrying publish..."
        spacetime publish --server "$server_url" --yes --delete-data=on-conflict $SPACETIMEDB_DATABASE
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
else
    echo ""
    echo "SpacetimeDB not running at $SPACETIMEDB_URL — skipping module publish."
    echo "  Start it with: spacetime start --listen-addr 127.0.0.1:18787 --data-dir ~/.bond/spacetimedb"
fi
