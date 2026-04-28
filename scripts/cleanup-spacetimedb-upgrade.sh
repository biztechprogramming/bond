#!/usr/bin/env bash
# Tear down local SpacetimeDB upgrade-trial state once validation is done.
# Removes the -next and legacy -test containers (if running) and their
# bind-mounted data dirs. Does NOT touch ~/.bond/spacetimedb (the local
# instance that mirrors prod) or anything on loki.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NEXT_COMPOSE="$PROJECT_ROOT/docker-compose.spacetimedb-next.yml"
NEXT_DATA="$HOME/.bond/spacetimedb-next"
TEST_DATA="$HOME/.bond/spacetimedb-test"

echo "This will stop and remove:"
echo "  - container  bond-spacetimedb-next (if running)"
echo "  - container  bond-spacetimedb-test (if running; legacy name)"
echo "  - directory  $NEXT_DATA"
echo "  - directory  $TEST_DATA"
echo
echo "It will NOT touch ~/.bond/spacetimedb (local prod-mirror data) or loki."
echo

if [ "${1:-}" != "-y" ]; then
    read -r -p "Proceed? (y/n) " -n 1 reply
    echo
    [[ $reply =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }
fi

# Prefer compose down (handles network teardown cleanly); fall back to docker rm.
if [ -f "$NEXT_COMPOSE" ]; then
    docker compose -f "$NEXT_COMPOSE" down 2>/dev/null || true
fi
docker rm -f bond-spacetimedb-next bond-spacetimedb-test 2>/dev/null || true

# Bind-mount dirs aren't managed by Docker — must remove explicitly.
rm -rf "$NEXT_DATA" "$TEST_DATA"

echo "Done."
