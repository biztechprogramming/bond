#!/bin/bash
set -euo pipefail

# setup-test-db.sh — Copy the test fixture DB to a temp location for runtime use.
# Idempotent: safe to run multiple times.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

SOURCE_DB="$PROJECT_ROOT/data/test-fixtures/bond-test.db"
TARGET_DIR="/tmp/visual-ui-test"
TARGET_DB="$TARGET_DIR/bond-test.db"

if [ ! -f "$SOURCE_DB" ]; then
    echo "ERROR: Source database not found: $SOURCE_DB" >&2
    exit 1
fi

mkdir -p "$TARGET_DIR"
cp "$SOURCE_DB" "$TARGET_DB"

export BOND_DATABASE_PATH="$TARGET_DB"
echo "BOND_DATABASE_PATH=$TARGET_DB"
