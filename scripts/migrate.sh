#!/bin/bash
# `make migrate` entry point.
#
# Primary action: publish the SpacetimeDB module (the schema source of truth).
# Secondary: optional SQLite vector-storage migrations, only when there's
# something to apply AND a migrate runner is available. Either step's failure
# does not block the other — they're independent.
#
# Configuration source: .env (BOND_SPACETIMEDB_URL, SPACETIMEDB_TOKEN). Falls
# back to bond.json only for the module name; the URL is owned by .env.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
MIGRATIONS_PATH="$PROJECT_ROOT/migrations"
BOND_HOME="${BOND_HOME:-$HOME/.bond}"
DATA_DIR="$BOND_HOME/data"
DB_FILE="$DATA_DIR/knowledge.db"

mkdir -p "$DATA_DIR"

# ─────────────────────────────────────────────────────────────────
# Load .env (the source of truth for service URLs and tokens).
# ─────────────────────────────────────────────────────────────────
# Snapshot caller-provided overrides before sourcing .env so they take
# precedence (e.g. `make migrate-staging` injects BOND_SPACETIMEDB_URL).
_CALLER_BOND_SPACETIMEDB_URL="${BOND_SPACETIMEDB_URL:-}"
_CALLER_SPACETIME_BIN="${SPACETIME_BIN:-}"

if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$PROJECT_ROOT/.env"
    set +a
fi

# Restore caller overrides on top of .env.
[ -n "$_CALLER_BOND_SPACETIMEDB_URL" ] && BOND_SPACETIMEDB_URL="$_CALLER_BOND_SPACETIMEDB_URL"
[ -n "$_CALLER_SPACETIME_BIN" ] && SPACETIME_BIN="$_CALLER_SPACETIME_BIN"

# Allow `make migrate-staging` (or any caller) to override the spacetime
# binary so we can publish from a non-default CLI version (e.g.
# spacetime-2.2.0 alongside the prod 2.0.2 binary).
SPACETIME_BIN="${SPACETIME_BIN:-spacetime}"

# Server URL: .env wins. BOND_SPACETIMEDB_URL is the canonical name; SPACETIMEDB_URL
# is accepted as a legacy alias. Last-resort fallback is localhost.
SPACETIMEDB_URL="${BOND_SPACETIMEDB_URL:-${SPACETIMEDB_URL:-http://localhost:18787}}"

# Module name: read from bond.json (config, not env).
SPACETIMEDB_MODULE_PATH="$PROJECT_ROOT/spacetimedb/spacetimedb"
SPACETIMEDB_DATABASE=$(python3 -c "import json,sys
try:
    print(json.load(open('$PROJECT_ROOT/spacetimedb/spacetime.local.json')).get('database',''))
except Exception:
    try:
        print(json.load(open('$PROJECT_ROOT/spacetimedb/spacetime.json')).get('database',''))
    except Exception:
        try:
            print(json.load(open('$PROJECT_ROOT/bond.json')).get('spacetimedb',{}).get('module',''))
        except Exception:
            sys.exit(1)
" 2>/dev/null)
if [ -z "$SPACETIMEDB_DATABASE" ]; then
    SPACETIMEDB_DATABASE="bond-core-v2"
fi

# ─────────────────────────────────────────────────────────────────
# 1. PRIMARY: publish SpacetimeDB module
# ─────────────────────────────────────────────────────────────────
publish_token_into_cli_config() {
    local token="$1"
    [ -z "$token" ] && return 0
    local cfg_dir="${HOME}/.config/spacetime"
    mkdir -p "$cfg_dir"
    echo "spacetimedb_token = \"$token\"" > "$cfg_dir/cli.toml"
}

spacetime_publish() {
    local server_url="$1"
    local output exit_code

    set +e
    output=$("$SPACETIME_BIN" publish --server "$server_url" --yes "$SPACETIMEDB_DATABASE" 2>&1)
    exit_code=$?
    set -e

    echo "$output"

    if [ $exit_code -ne 0 ] && echo "$output" | grep -qiE "401|Unauthorized|InvalidSignature|InvalidToken"; then
        echo "  Auth error detected — fetching fresh token from $server_url..."
        local fresh_token
        fresh_token=$(curl -s -X POST "$server_url/v1/identity" 2>/dev/null \
            | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])" 2>/dev/null)

        if [ -z "$fresh_token" ]; then
            echo "  Failed to fetch token from $server_url — is the server running?"
            return $exit_code
        fi
        publish_token_into_cli_config "$fresh_token"
        echo "  Retrying publish..."
        "$SPACETIME_BIN" publish --server "$server_url" --yes "$SPACETIMEDB_DATABASE"
        return $?
    fi
    return $exit_code
}

# 0 = success, 1 = real failure, 2 = skipped because server unreachable (not fatal)
PUBLISH_RC=0

# Seed the spacetime CLI with the token from .env so the first attempt can
# auth without a roundtrip to /v1/identity.
if [ -n "${SPACETIMEDB_TOKEN:-}" ]; then
    publish_token_into_cli_config "$SPACETIMEDB_TOKEN"
fi

if curl -s --max-time 3 "$SPACETIMEDB_URL/v1/health" > /dev/null 2>&1; then
    echo "Publishing SpacetimeDB module..."
    echo "  Module:   $SPACETIMEDB_DATABASE"
    echo "  Path:     $SPACETIMEDB_MODULE_PATH"
    echo "  Server:   $SPACETIMEDB_URL"

    set +e
    (cd "$SPACETIMEDB_MODULE_PATH" && spacetime_publish "$SPACETIMEDB_URL")
    PUBLISH_RC=$?
    set -e

    if [ $PUBLISH_RC -eq 0 ]; then
        echo "SpacetimeDB module published."
        echo ""
        echo "Regenerating SpacetimeDB TypeScript bindings..."
        # Four target dirs because the codebase has four binding consumers:
        #   - frontend/src/lib/spacetimedb       (Next.js project — primary)
        #   - gateway/src/spacetimedb            (gateway runtime — primary)
        #   - spacetimedb/gateway/src/spacetimedb (legacy mirror; kept in sync
        #     to avoid stale-build surprises until the duplicate cleanup lands)
        #   - spacetimedb/frontend/src/lib/spacetimedb (legacy mirror; same)
        set +e
        "$SPACETIME_BIN" generate --lang typescript --out-dir "$PROJECT_ROOT/frontend/src/lib/spacetimedb"            --module-path "$SPACETIMEDB_MODULE_PATH"
        "$SPACETIME_BIN" generate --lang typescript --out-dir "$PROJECT_ROOT/gateway/src/spacetimedb"                 --module-path "$SPACETIMEDB_MODULE_PATH"
        "$SPACETIME_BIN" generate --lang typescript --out-dir "$PROJECT_ROOT/spacetimedb/gateway/src/spacetimedb"     --module-path "$SPACETIMEDB_MODULE_PATH"
        "$SPACETIME_BIN" generate --lang typescript --out-dir "$PROJECT_ROOT/spacetimedb/frontend/src/lib/spacetimedb" --module-path "$SPACETIMEDB_MODULE_PATH"
        set -e
        echo "TypeScript bindings regenerated."

        # Seed initial provider catalog into SpacetimeDB (idempotent)
        echo ""
        echo "Seeding LLM providers into SpacetimeDB..."
        set +e
        (cd "$PROJECT_ROOT" && uv run python backend/seed_providers.py)
        SEED_RC=$?
        set -e
        if [ $SEED_RC -eq 0 ]; then
            echo "LLM providers seeded."
        else
            echo "WARNING: Provider seeding exited with status $SEED_RC (non-fatal)."
        fi
    else
        echo "WARNING: SpacetimeDB publish failed with status $PUBLISH_RC."
        PUBLISH_RC=1
    fi
else
    echo "SpacetimeDB not reachable at $SPACETIMEDB_URL — skipping publish."
    echo "  Start it with: make spacetimedb-up"
    PUBLISH_RC=2
fi

# ─────────────────────────────────────────────────────────────────
# 2. SECONDARY: SQLite vector-storage migrations (DISABLED)
# ─────────────────────────────────────────────────────────────────
# Disabled 2026-05-04 by user request: the embedding-DB migration path is
# inactive right now and the docker fallback (migrate/migrate) ships
# without the sqlite3 driver, so this section can't currently run end-to-
# end. Kept verbatim inside a `if false` gate so we don't lose state when
# we need to re-enable it.
if false; then
    # Pick the migrate runner. Priority: ~/go/bin/migrate, migrate on PATH,
    # then docker. If none, we silently skip — most runs of `make migrate`
    # don't have anything to apply, so a missing migrate CLI is not a defect.
    detect_migrate_runner() {
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
    }

    declare -a MIGRATE_CMD=()
    detect_migrate_runner

    TARGET_VERSION="$1"

    # Latest migration version on disk (e.g. "31" from 000031_*.up.sql).
    LATEST_DISK_VERSION=$(ls -1 "$MIGRATIONS_PATH"/*.up.sql 2>/dev/null \
        | sed 's|.*/||; s/_.*//' | sort -n | tail -1 | sed 's/^0*//')

    # Current applied version. Empty/0 if migrate not available or DB doesn't exist yet.
    CURRENT_VERSION=""
    if [ "$MIGRATE_RUNNER" != "none" ]; then
        set +e
        CURRENT_OUTPUT=$("${MIGRATE_CMD[@]}" -path "$MIGRATIONS_ARG" -database "$DB_URL_ARG" version 2>&1)
        set -e
        CURRENT_VERSION=$(echo "$CURRENT_OUTPUT" | grep -oE '^[0-9]+' || echo "")
    fi

    # Skip silently if SQLite is already up-to-date and no target was forced.
    if [ -z "$TARGET_VERSION" ] \
        && [ -n "$CURRENT_VERSION" ] && [ -n "$LATEST_DISK_VERSION" ] \
        && [ "$CURRENT_VERSION" = "$LATEST_DISK_VERSION" ]; then
        : # up-to-date — nothing to log; this is the common case
    elif [ "$MIGRATE_RUNNER" = "none" ] && [ -z "$TARGET_VERSION" ]; then
        # No runner and no explicit target — assume nothing to do.
        :
    elif [ "$MIGRATE_RUNNER" = "none" ]; then
        echo ""
        echo "SQLite migration requested (target=$TARGET_VERSION) but no migrate runner is available."
        echo "  Install a runner: 'make install-migrate' or ensure docker is on PATH."
        PUBLISH_RC=${PUBLISH_RC:-1}
    else
        echo ""
        echo "Running SQLite vector-storage migrations..."
        echo "  Runner:      $MIGRATE_RUNNER"
        echo "  Migrations:  $MIGRATIONS_PATH"
        echo "  SQLite file: $DB_FILE"

        set +e
        if [ -n "$TARGET_VERSION" ]; then
            if ! echo "$TARGET_VERSION" | grep -qE '^[0-9]+$'; then
                echo "Error: Target version must be a positive integer, got '$TARGET_VERSION'"
                exit 1
            fi
            if [ -z "$CURRENT_VERSION" ]; then
                echo "Error: Could not determine current SQLite migration version."
                echo "  Output: $CURRENT_OUTPUT"
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
            echo "  Forcing to version: $TARGET_VERSION (from $CURRENT_VERSION)"
            "${MIGRATE_CMD[@]}" -path "$MIGRATIONS_ARG" -database "$DB_URL_ARG" force "$TARGET_VERSION"
            SQLITE_RC=$?
        else
            echo "  Applying any pending migrations up to latest."
            "${MIGRATE_CMD[@]}" -path "$MIGRATIONS_ARG" -database "$DB_URL_ARG" up
            SQLITE_RC=$?
        fi
        set -e

        if [ "$SQLITE_RC" -eq 0 ]; then
            echo "SQLite migrations complete."
        else
            echo "WARNING: SQLite migrations exited with status $SQLITE_RC."
        fi
    fi
fi  # end SQLite migrations (disabled)

# Exit code: 0 success or skipped-because-offline; 1 if publish actually errored.
if [ "$PUBLISH_RC" = "1" ]; then
    exit 1
fi
exit 0
