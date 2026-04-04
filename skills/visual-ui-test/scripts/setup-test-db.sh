#!/bin/bash
set -euo pipefail

# setup-test-db.sh — Verify SpacetimeDB connectivity from inside the agent container.
# The test SpacetimeDB instance runs on the HOST (set up by setup-test-spacetimedb.sh).
# This script just confirms the container can reach it.

STDB_HOST="${BOND_TEST_STDB_HOST:-host.docker.internal}"
STDB_PORT="${BOND_TEST_STDB_PORT:-18797}"
STDB_URL="http://${STDB_HOST}:${STDB_PORT}"

echo "Checking SpacetimeDB connectivity at ${STDB_URL}..."

for i in $(seq 1 15); do
    if curl -sf "${STDB_URL}/v1/health" > /dev/null 2>&1; then
        echo "SpacetimeDB is reachable at ${STDB_URL}"
        echo "BOND_SPACETIMEDB_URL=${STDB_URL}"
        exit 0
    fi
    if [ "$i" -eq 15 ]; then
        echo "ERROR: Cannot reach SpacetimeDB at ${STDB_URL}" >&2
        echo "Ensure the host has run: skills/visual-ui-test/scripts/setup-test-spacetimedb.sh" >&2
        echo "And that BOND_TEST_STDB_HOST is set correctly for this container." >&2
        exit 1
    fi
    sleep 2
done
