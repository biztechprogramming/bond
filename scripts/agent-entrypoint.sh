#!/bin/bash
set -e

# Configure git identity (as root — will be copied to bond-agent below)
git config --global user.name "${AGENT_NAME:-bond-agent}"
git config --global user.email "${AGENT_EMAIL:-agent@bond.internal}"
git config --global --add safe.directory /bond
git config --global --add safe.directory /workspace

# Set up SSH from mounted keys
if [ -d "/tmp/.ssh" ]; then
    mkdir -p ~/.ssh
    cp /tmp/.ssh/* ~/.ssh/ 2>/dev/null || true
    chmod 700 ~/.ssh
    chmod 600 ~/.ssh/id_* 2>/dev/null || true
    # Only run ssh-keyscan if known_hosts doesn't already have github.com
    # (baked into image at build time to save ~1-2s network roundtrip)
    if ! grep -q "github.com" ~/.ssh/known_hosts 2>/dev/null; then
        ssh-keyscan -H github.com >> ~/.ssh/known_hosts 2>/dev/null || true
    fi
fi

# Use the bond repo at /bond.
# If not present, clone fresh (production/CI).
# If present and BOND_AUTO_PULL=1, pull latest on startup (non-mounted repos only).
if [ ! -d "/bond/.git" ]; then
    echo "[entrypoint] Cloning bond repo..."
    git clone "${BOND_REPO_URL:-git@github.com:biztechprogramming/bond.git}" /bond
    echo "[entrypoint] Clone complete."
else
    CURRENT_BRANCH=$(cd /bond && git branch --show-current 2>/dev/null || echo "unknown")
    echo "[entrypoint] Using bond repo (branch: $CURRENT_BRANCH)"

    # Auto-pull if enabled and /bond is NOT a host bind mount.
    # Detect bind mount: if /bond is on a different device than /, it's mounted from host.
    _bond_dev=$(stat -c '%d' /bond 2>/dev/null)
    if [ "${BOND_AUTO_PULL:-0}" = "1" ] && [ "$_bond_dev" = "$_root_dev" ]; then
        echo "[entrypoint] BOND_AUTO_PULL=1 — pulling latest on branch $CURRENT_BRANCH..."
        cd /bond && git fetch origin && git reset --hard "origin/$CURRENT_BRANCH" 2>/dev/null || true
        echo "[entrypoint] Pull complete."
    elif [ "${BOND_AUTO_PULL:-0}" = "1" ]; then
        echo "[entrypoint] BOND_AUTO_PULL=1 but /bond is a host mount — skipping pull (would mutate host)"
    fi
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

# ---------------------------------------------------------------------------
# Privilege drop (037 §4.4.3)
# ---------------------------------------------------------------------------
# Grant bond-agent access to mounted volumes via group membership rather
# than chown (which would change ownership on the host side).
#
# Strategy: detect the GID of each mounted dir, create a matching group
# inside the container, and add bond-agent to it. This gives read/write
# access without changing file ownership.

_add_bond_agent_to_gid() {
    local dir="$1"
    [ -d "$dir" ] || return 0
    local gid
    gid=$(stat -c '%g' "$dir" 2>/dev/null) || return 0

    # Skip if bond-agent is already in a group with this GID
    if id -G bond-agent 2>/dev/null | tr ' ' '\n' | grep -qx "$gid"; then
        return 0
    fi

    # Create a group for this GID if it doesn't exist
    local grp_name
    grp_name=$(getent group "$gid" | cut -d: -f1 2>/dev/null)
    if [ -z "$grp_name" ]; then
        grp_name="hostmount_${gid}"
        groupadd -g "$gid" "$grp_name" 2>/dev/null || true
    fi

    usermod -aG "$grp_name" bond-agent 2>/dev/null || true
    echo "[entrypoint] Added bond-agent to group $grp_name (gid=$gid) for $dir"
}

_add_bond_agent_to_gid /bond
_add_bond_agent_to_gid /workspace
_add_bond_agent_to_gid /data
_add_bond_agent_to_gid /config

# /data may be a fresh container volume with root ownership — bond-agent
# needs to write here (agent DB, logs). Only chown dirs that are NOT
# host mounts (i.e., Docker-managed volumes or dirs created by the image).
# Detect by checking if the dir is on the same device as /.
_root_dev=$(stat -c '%d' / 2>/dev/null)
for dir in /data /data/shared; do
    if [ -d "$dir" ]; then
        _dir_dev=$(stat -c '%d' "$dir" 2>/dev/null)
        if [ "$_dir_dev" = "$_root_dev" ]; then
            # Same device as / → likely created by Dockerfile, safe to chown
            chown bond-agent:bond-agent "$dir" 2>/dev/null || true
        fi
    fi
done

# Copy git/ssh config to bond-agent user
if [ -d /root/.ssh ]; then
    mkdir -p /home/bond-agent/.ssh
    cp -r /root/.ssh/* /home/bond-agent/.ssh/ 2>/dev/null || true
    chown -R bond-agent:bond-agent /home/bond-agent/.ssh
    chmod 700 /home/bond-agent/.ssh
    chmod 600 /home/bond-agent/.ssh/id_* 2>/dev/null || true
fi
cp /root/.gitconfig /home/bond-agent/.gitconfig 2>/dev/null || true
chown bond-agent:bond-agent /home/bond-agent/.gitconfig 2>/dev/null || true

# Mark /bond and /workspace as safe for git under the bond-agent user too
# Write directly instead of spawning su + git subprocesses (~0.5s saved)
BOND_AGENT_GITCONFIG="/home/bond-agent/.gitconfig"
if ! grep -q "safe" "$BOND_AGENT_GITCONFIG" 2>/dev/null; then
    cat >> "$BOND_AGENT_GITCONFIG" <<'EOF'
[safe]
	directory = /bond
	directory = /workspace
EOF
    chown bond-agent:bond-agent "$BOND_AGENT_GITCONFIG" 2>/dev/null || true
fi

# Drop privileges and exec worker as bond-agent
exec gosu bond-agent python -m backend.app.worker "$@"
