#!/bin/bash
set -euo pipefail

# teardown-test-spacetimedb.sh — HOST-SIDE script to stop and remove the
# test SpacetimeDB container. Does NOT delete the data volume.

CONTAINER_NAME="bond-test-spacetimedb"

if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "Stopping and removing ${CONTAINER_NAME}..."
    docker rm -f "$CONTAINER_NAME"
    echo "Done. Container removed."
    echo "Data volume at ~/.bond/spacetimedb-test is preserved."
    echo "To delete data: rm -rf ~/.bond/spacetimedb-test"
else
    echo "No container named '${CONTAINER_NAME}' found."
fi
