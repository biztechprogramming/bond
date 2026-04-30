#!/bin/bash
# Run local SQLite vector-storage migrations and SpacetimeDB module publish.
#
# Auto-detects how to run the golang-migrate tool:
#   1. Local Go-installed binary at ~/go/bin/migrate (fastest)
#   2. `migrate` already on PATH
#   3. Docker image migrate/migrate (no local Go install needed)
#
# SQLite migration failures do not block the SpacetimeDB publish — the two
# are independent and either may be useful in isolation.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
MIGRATIONS_PATH="$PROJECT_ROOT/migrations"
BOND_HOME="${BOND_HOME:-$HOME/.bond}"
DATA_DIR="$BOND_HOME/data"
DB_FILE="$DATA_DIR/knowledge.db"

mkdir -p "$DATA_DIR"

# ── Pick a migrate runner ─────────────────────────────────────────
# Sets MIGRATE_CMD (array), MIGRATIONS_ARG, DB_URL_ARG, MIGRATE_RUNNER.
MIGRATE_RUNNER=""
declare -a MIGRATE_CMD=()

if [ -x "$HOME/go/bin/migrate" ]; then
    MIGRATE_CMD=("$HOME/go/bin/migrate")
    MIGRATIONS_ARG="$MIGRATIONS_PATH"
    DB_URL_ARG="sqlite3://$DB_FILE"
    MIGRATE_RUNNER="local ($HOME/go/bin/migrate)"
elif command -v migrate &> /dev/null; then
    MIGRATE_CMD=("migrate")
    MIGRATIONS_ARG="$MIGRATIONS_PATH"
    DB_URL_ARG="sqlite3://$DB_FILE"
    MIGRATE_RUNNER="local (migrate on PATH)"
elif command -v docker &> /dev/null; then
    # The migrate/migrate image bundles all drivers including SQLite.
    # We bind-mount the host paths so it operates on the same files the
    # local binary would.
    MIGRATE_CMD=(docker run --rm
        -v "$MIGRATIONS_PATH:/migrations:ro"
        -v "$DATA_DIR:/data"
        migrate/migrate)
    MIGRATIONS_ARG="/migrations"
    DB_URL_ARG="sqlite3:///data/knowledge.db"
    MIGRATE_RUNNER="docker (migrate/migrate)"
else
    MIGRATE_RUNNER="none"
fi

# ── Run SQLite migrations ─────────────────────────────────────────
TARGET_VERSION="$1"

if [ "$MIGRATE_RUNNER" = "none" ]; then
    echo "Skipping SQLite vector-storage migrations: neither 'migrate' nor docker available."
    echo "  Install Docker, or run 'make install-migrate' for the local binary."
else
    echo "Running local SQLite vector-storage migrations..."
    echo "  Runner:      $MIGRATE_RUNNER"
    echo "  Migrations:  $MIGRATIONS_PATH"
    echo "  SQLite file: $DB_FILE"

    # Don't let a SQLite migration failure abort the spacetime publish.
    set +e
    if [ -n "$TARGET_VERSION" ]; then
        if ! echo "$TARGET_VERSION" | grep -qE '^[0-9]+$'; then
            echo "Error: Target version must be a positive integer, got '$TARGET_VERSION'"
            exit 1
        fi

        CURRENT_OUTPUT=$("${MIGRATE_CMD[@]}" -path "$MIGRATIONS_ARG" -database "$DB_URL_ARG" version 2>&1 || true)
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
        "${MIGRATE_CMD[@]}" -path "$MIGRATIONS_ARG" -database "$DB_URL_ARG" force "$TARGET_VERSION"
        SQLITE_RC=$?
    else
        echo "  Skipping version check — running all pending migrations up to latest."
        "${MIGRATE_CMD[@]}" -path "$MIGRATIONS_ARG" -database "$DB_URL_ARG" up
        SQLITE_RC=$?
    fi
    set -e

    if [ "$SQLITE_RC" -eq 0 ]; then
        echo "Local SQLite vector-storage migrations complete."
    else
        echo "WARNING: SQLite migrations exited with status $SQLITE_RC — continuing to SpacetimeDB publish anyway."
    fi
fi

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
