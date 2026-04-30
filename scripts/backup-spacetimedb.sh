#!/bin/bash
set -e

# backup-spacetimedb.sh
# Performs a backup of the SpacetimeDB data directory and maintains a rotation.
# Refuses to back up an empty database to prevent overwriting good backups.

BACKUP_DIR="$HOME/.bond/backups/spacetimedb"
DATA_DIR="$HOME/.bond/spacetimedb"
CONFIG_DIR="$HOME/.bond/spacetimedb-config"
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

# Ensure backup directories exist
mkdir -p "$BACKUP_DIR/daily"
mkdir -p "$BACKUP_DIR/weekly"
mkdir -p "$BACKUP_DIR/monthly"

# 1. Perform Backups
# Data and config are written as separate tarballs so they can be restored
# independently — the keys (id_ecdsa) sometimes need to be restored alone
# after a logout/reset, without rolling back data.
# We use -C to change directory so the tarballs don't contain absolute paths.
DATA_BACKUP="$BACKUP_DIR/daily/spacetimedb_$TIMESTAMP.tar.gz"
CONFIG_BACKUP="$BACKUP_DIR/daily/spacetimedb-config_$TIMESTAMP.tar.gz"

tar -czf "$DATA_BACKUP" -C "$DATA_DIR" .
echo "Data backup created: $DATA_BACKUP"

if [ -d "$CONFIG_DIR" ]; then
    tar -czf "$CONFIG_BACKUP" -C "$CONFIG_DIR" .
    echo "Config backup created: $CONFIG_BACKUP"
else
    echo "Warning: $CONFIG_DIR not found — skipping config backup"
fi

# 2. Rotation Logic (Keep last 5 of each prefix)
rotate_backups() {
    local folder=$1
    local prefix=$2
    local count=$3
    cd "$folder"
    ls -t ${prefix}_*.tar.gz 2>/dev/null | tail -n +$((count + 1)) | xargs -r rm
}

# Daily rotation
rotate_backups "$BACKUP_DIR/daily" "spacetimedb" 5
rotate_backups "$BACKUP_DIR/daily" "spacetimedb-config" 5

# Weekly Promotion (Run on Sundays)
if [ "$(date +%u)" -eq 7 ]; then
    cp "$DATA_BACKUP" "$BACKUP_DIR/weekly/"
    [ -f "$CONFIG_BACKUP" ] && cp "$CONFIG_BACKUP" "$BACKUP_DIR/weekly/"
    rotate_backups "$BACKUP_DIR/weekly" "spacetimedb" 5
    rotate_backups "$BACKUP_DIR/weekly" "spacetimedb-config" 5
fi

# Monthly Promotion (Run on the 1st)
if [ "$(date +%d)" -eq 01 ]; then
    cp "$DATA_BACKUP" "$BACKUP_DIR/monthly/"
    [ -f "$CONFIG_BACKUP" ] && cp "$CONFIG_BACKUP" "$BACKUP_DIR/monthly/"
    rotate_backups "$BACKUP_DIR/monthly" "spacetimedb" 5
    rotate_backups "$BACKUP_DIR/monthly" "spacetimedb-config" 5
fi
