#!/bin/bash
set -euo pipefail

# migrate-schema.sh
#
# Safely migrates SpacetimeDB data across breaking schema changes.
#
# Flow:
#   1. Dump all tables from the live database to JSON files
#   2. Run `spacetime publish --delete-data` with the new schema
#   3. Re-import the dumped data via import reducers (with schema migration)
#
# Usage:
#   ./scripts/migrate-schema.sh                    # dump + publish + import
#   ./scripts/migrate-schema.sh --dump-only        # just dump (for testing)
#   ./scripts/migrate-schema.sh --import-only DIR  # re-import from a previous dump
#
# Requirements:
#   - spacetime CLI configured with server "bond-local"
#   - STDB module source at spacetimedb/spacetimedb/

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MODULE_DIR="$REPO_DIR/spacetimedb"
STDB_SERVER="${BOND_STDB_SERVER:-bond-local}"
STDB_MODULE="${BOND_SPACETIMEDB_MODULE:-bond-core-v2}"
STDB_URL="${BOND_SPACETIMEDB_URL:-http://localhost:18787}"
DUMP_BASE="$HOME/.bond/backups/spacetimedb/migrations"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DUMP_DIR="$DUMP_BASE/$TIMESTAMP"

# Tables to dump/restore, in dependency order
TABLES=(
  agents
  agent_channels
  agent_workspace_mounts
  providers
  provider_api_keys
  provider_aliases
  llm_models
  settings
  prompt_fragments
  prompt_templates
  prompt_fragment_versions
  prompt_template_versions
  agent_prompt_fragments
  conversations
  conversation_messages
  work_plans
  work_items
)

# ─── Helpers ─────────────────────────────────────────────────────────────────

log() { echo "[migrate] $*"; }
err() { echo "[migrate] ERROR: $*" >&2; }

# Dump a single table to JSON via the SpacetimeDB HTTP SQL API
dump_table() {
  local table=$1
  local outfile="$DUMP_DIR/${table}.json"

  # Use curl against the HTTP API to get raw JSON response
  local response
  response=$(curl -s -X POST \
    -H "Content-Type: application/json" \
    "$STDB_URL/v1/database/$STDB_MODULE/sql" \
    -d "SELECT * FROM $table" 2>/dev/null) || true

  if [ -z "$response" ] || [ "$response" = "null" ]; then
    log "  $table: empty or unreachable"
    echo "[]" > "$outfile"
    return
  fi

  # Parse the SpacetimeDB SQL response format:
  # [{ "schema": { "elements": [...] }, "rows": [[...], ...] }]
  # Convert to array of objects
  local rows
  rows=$(echo "$response" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    if not data or not isinstance(data, list) or len(data) == 0:
        print('[]')
        sys.exit(0)
    result_set = data[0]
    if 'rows' not in result_set or 'schema' not in result_set:
        print('[]')
        sys.exit(0)
    columns = []
    for elem in result_set['schema']['elements']:
        name = elem.get('name', {})
        if isinstance(name, dict) and 'some' in name:
            columns.append(name['some'])
        else:
            columns.append(str(name))
    rows = []
    for row in result_set['rows']:
        obj = {}
        for i, col in enumerate(columns):
            obj[col] = row[i] if i < len(row) else None
        rows.append(obj)
    json.dump(rows, sys.stdout, ensure_ascii=False)
except Exception as e:
    print(f'[]', file=sys.stdout)
    print(f'Parse error: {e}', file=sys.stderr)
" 2>/dev/null) || rows="[]"

  echo "$rows" > "$outfile"
  local count
  count=$(echo "$rows" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
  log "  $table: $count rows"
}

# Import a single table from JSON dump via the Gateway's import API
# We call reducers via the SpacetimeDB HTTP API directly
import_table() {
  local table=$1
  local infile="$2/${table}.json"

  if [ ! -f "$infile" ]; then
    log "  $table: no dump file, skipping"
    return
  fi

  local count
  count=$(python3 -c "import sys,json; print(len(json.load(open('$infile'))))" 2>/dev/null || echo "0")
  if [ "$count" = "0" ]; then
    log "  $table: 0 rows, skipping"
    return
  fi

  log "  $table: importing $count rows..."

  # Use the gateway's schema-versions.ts logic via a Node.js helper
  # This handles field mapping, defaults, and option encoding
  node --input-type=module <<IMPORT_SCRIPT
import { readFileSync } from "fs";
const { getCurrentTables, mapRowToCurrentSchema } = await import("$REPO_DIR/gateway/dist/backups/schema-versions.js");

const STDB_URL = "$STDB_URL";
const STDB_MODULE = "$STDB_MODULE";

// Read token from spacetime CLI config
let token = "";
try {
  const toml = readFileSync(process.env.HOME + "/.config/spacetime/cli.toml", "utf8");
  const match = toml.match(/spacetimedb_token\\s*=\\s*"([^"]+)"/);
  if (match) token = match[1];
} catch {}

const rows = JSON.parse(readFileSync("$infile", "utf8"));
const tableDef = getCurrentTables().find(t => t.table === "$table");
if (!tableDef) { console.error("Unknown table: $table"); process.exit(1); }

let ok = 0, fail = 0;
for (const row of rows) {
  try {
    const args = mapRowToCurrentSchema("$table", row);
    const url = STDB_URL + "/v1/database/" + STDB_MODULE + "/call/" + tableDef.importReducer;
    const headers = { "Content-Type": "application/json" };
    if (token) headers["Authorization"] = "Bearer " + token;
    const res = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(args, (_k, v) => typeof v === "bigint" ? Number(v) : v),
    });
    if (!res.ok) {
      const body = await res.text();
      throw new Error(res.status + ": " + body.slice(0, 200));
    }
    ok++;
  } catch (e) {
    fail++;
    if (fail <= 3) console.error("  Failed:", row.id || row.key || "?", e.message?.slice(0, 150));
  }
}
console.log("  " + "$table" + ": " + ok + " imported, " + fail + " failed");
if (fail > 0) process.exit(1);
IMPORT_SCRIPT
}

# ─── Main ────────────────────────────────────────────────────────────────────

MODE="full"
IMPORT_DIR=""

if [ "${1:-}" = "--dump-only" ]; then
  MODE="dump"
elif [ "${1:-}" = "--import-only" ]; then
  MODE="import"
  IMPORT_DIR="${2:-}"
  if [ -z "$IMPORT_DIR" ] || [ ! -d "$IMPORT_DIR" ]; then
    err "Usage: $0 --import-only <dump-directory>"
    exit 1
  fi
fi

# ─── Step 1: Dump ────────────────────────────────────────────────────────────

if [ "$MODE" != "import" ]; then
  log "═══════════════════════════════════════════════════════"
  log "Step 1: Dumping all tables from $STDB_MODULE"
  log "═══════════════════════════════════════════════════════"

  mkdir -p "$DUMP_DIR"

  for table in "${TABLES[@]}"; do
    dump_table "$table"
  done

  log ""
  log "Dump saved to: $DUMP_DIR"
  log ""

  if [ "$MODE" = "dump" ]; then
    log "Dump-only mode — done."
    exit 0
  fi

  IMPORT_DIR="$DUMP_DIR"
fi

# ─── Step 2: Publish with --delete-data ──────────────────────────────────────

if [ "$MODE" = "full" ]; then
  log "═══════════════════════════════════════════════════════"
  log "Step 2: Publishing new schema with --delete-data"
  log "═══════════════════════════════════════════════════════"
  log ""
  log "⚠️  This will DELETE ALL DATA in $STDB_MODULE and republish."
  log "   Dump is saved at: $DUMP_DIR"
  log ""
  read -p "Type YES to continue: " confirm
  if [ "$confirm" != "YES" ]; then
    log "Aborted."
    exit 1
  fi

  cd "$MODULE_DIR"
  spacetime publish --delete-data always -s "$STDB_SERVER" "$STDB_MODULE"

  log "Schema published. Database is now empty."
  log ""

  # Wait a moment for the module to be ready
  sleep 2
fi

# ─── Step 3: Import ──────────────────────────────────────────────────────────

log "═══════════════════════════════════════════════════════"
log "Step 3: Importing data from dump"
log "═══════════════════════════════════════════════════════"

# Build the backup schema-versions module
log "Building schema-versions..."
cd "$REPO_DIR/gateway"
npx esbuild src/backups/schema-versions.ts --outfile=dist/backups/schema-versions.js --format=esm --platform=node --sourcemap 2>/dev/null || {
  err "Failed to build schema-versions.ts"
  exit 1
}

IMPORT_ERRORS=0
for table in "${TABLES[@]}"; do
  import_table "$table" "$IMPORT_DIR" || IMPORT_ERRORS=$((IMPORT_ERRORS + 1))
done

log ""
if [ "$IMPORT_ERRORS" -gt 0 ]; then
  log "⚠️  $IMPORT_ERRORS table(s) had import errors."
  log "   Dump is preserved at: $IMPORT_DIR"
  log "   Re-run with: $0 --import-only $IMPORT_DIR"
  exit 1
else
  log "✅ All tables imported successfully."
fi
