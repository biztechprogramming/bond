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

# Clone or pull the bond repo into /bond
if [ ! -d "/bond/.git" ]; then
    echo "[entrypoint] Cloning bond repo..."
    git clone "${BOND_REPO_URL:-git@github.com:biztechprogramming/bond.git}" /bond
    echo "[entrypoint] Clone complete."
else
    echo "[entrypoint] Pulling latest main..."
    cd /bond && git checkout main && git pull origin main --ff-only || echo "[entrypoint] Pull failed, continuing with existing clone."
fi

# Execute worker
exec python -m backend.app.worker "$@"
