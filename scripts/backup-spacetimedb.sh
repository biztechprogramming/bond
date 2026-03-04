#!/bin/bash
set -e

# backup-spacetimedb.sh
# Performs a backup of the SpacetimeDB data directory and maintains a rotation.

BACKUP_DIR="$HOME/.bond/backups/spacetimedb"
DATA_DIR="$HOME/.bond/spacetimedb"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

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
