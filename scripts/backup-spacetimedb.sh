#!/bin/bash
set -e

# backup-spacetimedb.sh
# Performs a backup of the SpacetimeDB data directory and maintains a rotation.
# Refuses to back up an empty database to prevent overwriting good backups.

BACKUP_DIR="$HOME/.bond/backups/spacetimedb"
DATA_DIR="$HOME/.bond/spacetimedb"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Guard: check that the database has data before backing up.
# This prevents a freshly-wiped database from rotating out good backups.
# We check for agents OR conversations — a DB with config but no conversations
# is still worth backing up.
STDB_URL="${BOND_SPACETIMEDB_URL:-http://localhost:18787}"
STDB_MODULE="${BOND_SPACETIMEDB_MODULE:-bond-core-v2}"

# Query via HTTP API (avoids CLI token/auth issues)
_sql_count() {
  curl -s -X POST "$STDB_URL/v1/database/$STDB_MODULE/sql" -d "$1" 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['rows'][0][0])" 2>/dev/null || echo "0"
}

AGENT_COUNT=$(_sql_count "SELECT COUNT(*) AS cnt FROM agents")
CONV_COUNT=$(_sql_count "SELECT COUNT(*) AS cnt FROM conversations")

TOTAL=$((AGENT_COUNT + CONV_COUNT))
if [ "$TOTAL" = "0" ] || [ -z "$TOTAL" ]; then
  echo "Skipping backup: database has 0 agents and 0 conversations (empty or unreachable)"
  exit 0
fi

echo "Database has $AGENT_COUNT agents, $CONV_COUNT conversations — proceeding with backup"

# Ensure backup directory exists
mkdir -p "$BACKUP_DIR/daily"
mkdir -p "$BACKUP_DIR/weekly"
mkdir -p "$BACKUP_DIR/monthly"

# 1. Perform Backup (tar the data directory)
# We use -C to change directory so the tarball doesn't contain absolute paths
tar -czf "$BACKUP_DIR/daily/spacetimedb_$TIMESTAMP.tar.gz" -C "$DATA_DIR" .

echo "Backup created: $BACKUP_DIR/daily/spacetimedb_$TIMESTAMP.tar.gz"

# 2. Rotation Logic (Keep last 5)
rotate_backups() {
    local folder=$1
    local count=$2
    cd "$folder"
    ls -t | tail -n +$((count + 1)) | xargs -r rm
}

# Daily rotation
rotate_backups "$BACKUP_DIR/daily" 5

# Weekly Promotion (Run on Sundays)
if [ "$(date +%u)" -eq 7 ]; then
    cp "$BACKUP_DIR/daily/spacetimedb_$TIMESTAMP.tar.gz" "$BACKUP_DIR/weekly/"
    rotate_backups "$BACKUP_DIR/weekly" 5
fi

# Monthly Promotion (Run on the 1st)
if [ "$(date +%d)" -eq 01 ]; then
    cp "$BACKUP_DIR/daily/spacetimedb_$TIMESTAMP.tar.gz" "$BACKUP_DIR/monthly/"
    rotate_backups "$BACKUP_DIR/monthly" 5
fi
