#!/bin/bash
set -euo pipefail

# setup-test-spacetimedb.sh — HOST-SIDE script to start a test SpacetimeDB
# instance on port 18797 (separate from production on 18787).
# Run this on the host machine BEFORE starting the agent container.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

TEST_PORT=18797
CONTAINER_NAME="bond-test-spacetimedb"
STDB_IMAGE="clockworklabs/spacetime:v2.0.2"
DATA_VOLUME="$HOME/.bond/spacetimedb-test"
MODULE_NAME="bond-core-v2"
MODULE_DIR="$PROJECT_ROOT/spacetimedb/spacetimedb"

echo "=== Bond Test SpacetimeDB Setup ==="

# --- Step 1: Check if already running ---
if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    if curl -sf "http://localhost:${TEST_PORT}/v1/health" > /dev/null 2>&1; then
        echo "Test SpacetimeDB is already running on port ${TEST_PORT}."
    else
        echo "Container exists but health check failed. Restarting..."
        docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
    fi
fi

# --- Step 2: Start container if not running ---
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    # Clean up stopped container with same name
    docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

    echo "Starting SpacetimeDB on port ${TEST_PORT}..."
    mkdir -p "$DATA_VOLUME"
    docker run -d \
        --name "$CONTAINER_NAME" \
        -p "${TEST_PORT}:3000" \
        -v "${DATA_VOLUME}:/home/spacetime/.local/share/spacetime/data" \
        "$STDB_IMAGE" \
        start

    # Wait for health
    echo "Waiting for SpacetimeDB to be ready..."
    for i in $(seq 1 30); do
        if curl -sf "http://localhost:${TEST_PORT}/v1/health" > /dev/null 2>&1; then
            echo "SpacetimeDB is healthy."
            break
        fi
        if [ "$i" -eq 30 ]; then
            echo "ERROR: SpacetimeDB failed to start after 60s." >&2
            docker logs "$CONTAINER_NAME" 2>&1 | tail -20
            exit 1
        fi
        sleep 2
    done
fi

# --- Step 3: Publish the SpacetimeDB module ---
if ! command -v spacetime &> /dev/null; then
    echo "WARNING: 'spacetime' CLI not found. Cannot publish module." >&2
    echo "Install it: curl -sSf https://install.spacetimedb.com | sh" >&2
    echo "Then re-run this script to publish the module."
    exit 1
fi

echo "Publishing module '${MODULE_NAME}' to test instance..."
cd "$MODULE_DIR"
spacetime publish "$MODULE_NAME" --server "http://localhost:${TEST_PORT}" --yes 2>&1 || {
    echo "WARNING: Module publish failed (may already be published). Continuing..."
}
cd "$PROJECT_ROOT"

# --- Step 4: Seed test data ---
echo "Seeding test data..."
SPACETIMEDB_URL="http://localhost:${TEST_PORT}" \
SPACETIMEDB_DATABASE="$MODULE_NAME" \
bash "$PROJECT_ROOT/scripts/seed-spacetimedb.sh" 2>&1 || {
    echo "WARNING: Seed script failed (data may already exist). Continuing..."
}

echo ""
echo "=== Test SpacetimeDB is ready ==="
echo "  URL:       http://localhost:${TEST_PORT}"
echo "  Module:    ${MODULE_NAME}"
echo "  Container: ${CONTAINER_NAME}"
echo "  Data:      ${DATA_VOLUME}"
echo ""
echo "Pass BOND_TEST_STDB_HOST=<hostname> to the agent container"
echo "so scripts inside can reach this instance."
