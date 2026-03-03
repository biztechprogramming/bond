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

if curl -s "$SPACETIMEDB_URL/v1/health" > /dev/null 2>&1; then
    echo ""
    echo "Publishing SpacetimeDB module..."
    echo "  Module: $SPACETIMEDB_MODULE"
    echo "  Server: $SPACETIMEDB_URL"
    cd "$SPACETIMEDB_MODULE"
    spacetime publish --server "$SPACETIMEDB_URL" --yes
    echo "SpacetimeDB migrations complete."
else
    echo ""
    echo "SpacetimeDB not running at $SPACETIMEDB_URL — skipping module publish."
    echo "  Start it with: spacetime start --listen-addr 127.0.0.1:18787 --data-dir ~/.bond/spacetimedb"
fi
