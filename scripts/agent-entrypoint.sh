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

# --- OpenSandbox execd (code execution daemon) ---
# Start execd in background if the binary is present.
# Provides structured command execution, file ops, and code interpreter
# on port 44772 (internal only, not exposed to host).
if [ -x /opt/opensandbox/execd ]; then
    # Set up env file for execd to discover runtime PATH
    EXECD_ENVS="/opt/opensandbox/.env"
    mkdir -p /opt/opensandbox
    printf 'PATH=%s\n' "$PATH" > "$EXECD_ENVS"
    export EXECD_ENVS

    echo "[entrypoint] Starting execd on port ${EXECD_PORT:-44772}..."
    /opt/opensandbox/execd --port "${EXECD_PORT:-44772}" &
    EXECD_PID=$!
    echo "[entrypoint] execd started (pid=$EXECD_PID)"
fi

# --- Jupyter code interpreter ---
# Start Jupyter in background if installed (enables stateful code execution).
# Only started when BOND_CODE_INTERPRETER=1 is set (opt-in to avoid overhead).
if [ "${BOND_CODE_INTERPRETER:-0}" = "1" ] && command -v jupyter &>/dev/null; then
    JUPYTER_PORT="${JUPYTER_PORT:-44771}"
    JUPYTER_TOKEN="${JUPYTER_TOKEN:-bond}"

    echo "[entrypoint] Starting Jupyter on port $JUPYTER_PORT..."
    jupyter notebook --ip=127.0.0.1 --port="$JUPYTER_PORT" \
        --allow-root --no-browser \
        --NotebookApp.token="$JUPYTER_TOKEN" \
        > /tmp/jupyter.log 2>&1 &
    JUPYTER_PID=$!
    echo "[entrypoint] Jupyter started (pid=$JUPYTER_PID)"
fi

# Execute worker
exec python -m backend.app.worker "$@"
