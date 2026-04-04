#!/bin/bash
set -euo pipefail

# generate-test-db.sh — HOST-SIDE script to set up a test SpacetimeDB instance.
# This is a convenience wrapper around setup-test-spacetimedb.sh.
#
# What it does:
#   1. Starts a SpacetimeDB Docker container on port 18797
#   2. Publishes the bond-core-v2 module to it
#   3. Seeds it with test data via the SpacetimeDB HTTP SQL API
#
# Run on the HOST machine, NOT inside the agent container.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "generate-test-db.sh: delegating to setup-test-spacetimedb.sh"
exec bash "$SCRIPT_DIR/setup-test-spacetimedb.sh"
