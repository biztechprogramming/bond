#!/bin/bash
set -e

# Configure git identity
git config --global user.name "${AGENT_NAME:-bond-agent}"
git config --global user.email "${AGENT_EMAIL:-agent@bond.internal}"
git config --global --add safe.directory /bond

# Set up SSH from mounted keys
if [ -d "/tmp/.ssh" ]; then
    mkdir -p ~/.ssh
    cp /tmp/.ssh/* ~/.ssh/ 2>/dev/null || true
    chmod 700 ~/.ssh
    chmod 600 ~/.ssh/id_* 2>/dev/null || true
    ssh-keyscan -H github.com >> ~/.ssh/known_hosts 2>/dev/null || true
fi

# Use the bond repo at /bond.
# If mounted from host (development), use it as-is — never switch branches
# or pull, as that would mutate the host working directory.
# If not present, clone fresh (production/CI).
if [ ! -d "/bond/.git" ]; then
    echo "[entrypoint] Cloning bond repo..."
    git clone "${BOND_REPO_URL:-git@github.com:biztechprogramming/bond.git}" /bond
    echo "[entrypoint] Clone complete."
else
    CURRENT_BRANCH=$(cd /bond && git branch --show-current 2>/dev/null || echo "unknown")
    echo "[entrypoint] Using mounted bond repo (branch: $CURRENT_BRANCH)"
fi

# Execute worker
exec python -m backend.app.worker "$@"
