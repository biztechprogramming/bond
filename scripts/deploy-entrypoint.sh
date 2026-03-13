#!/bin/bash
set -e

# ---------------------------------------------------------------------------
# Bond Deployment Agent Entrypoint
# Slimmed-down version of agent-entrypoint.sh for deploy-{env} agents.
# No SSH, no git clone/pull, no execd, no Jupyter.
# ---------------------------------------------------------------------------

# Configure git identity (read-only browsing only)
git config --global user.name "${AGENT_NAME:-deploy-agent}"
git config --global user.email "${AGENT_EMAIL:-deploy@bond.internal}"
git config --global --add safe.directory /bond
git config --global --add safe.directory /workspace

# Verify bond repo is mounted
if [ ! -d "/bond/.git" ]; then
    echo "[deploy-entrypoint] ERROR: /bond must be mounted (read-only). No clone support."
    exit 1
fi

CURRENT_BRANCH=$(cd /bond && git branch --show-current 2>/dev/null || echo "unknown")
echo "[deploy-entrypoint] Bond repo mounted (branch: $CURRENT_BRANCH)"
echo "[deploy-entrypoint] Environment: ${BOND_DEPLOY_ENV:-NOT SET}"

# ---------------------------------------------------------------------------
# Privilege drop — same group-based strategy as agent-entrypoint.sh
# ---------------------------------------------------------------------------
_add_bond_agent_to_gid() {
    local dir="$1"
    [ -d "$dir" ] || return 0
    local gid
    gid=$(stat -c '%g' "$dir" 2>/dev/null) || return 0

    if id -G bond-agent 2>/dev/null | tr ' ' '\n' | grep -qx "$gid"; then
        return 0
    fi

    local grp_name
    grp_name=$(getent group "$gid" | cut -d: -f1 2>/dev/null)
    if [ -z "$grp_name" ]; then
        grp_name="hostmount_${gid}"
        groupadd -g "$gid" "$grp_name" 2>/dev/null || true
    fi

    usermod -aG "$grp_name" bond-agent 2>/dev/null || true
    echo "[deploy-entrypoint] Added bond-agent to group $grp_name (gid=$gid) for $dir"
}

_add_bond_agent_to_gid /bond
_add_bond_agent_to_gid /workspace
_add_bond_agent_to_gid /data
_add_bond_agent_to_gid /config

# Handle Docker-managed volumes (safe to chown)
_root_dev=$(stat -c '%d' / 2>/dev/null)
for dir in /data /data/shared; do
    if [ -d "$dir" ]; then
        _dir_dev=$(stat -c '%d' "$dir" 2>/dev/null)
        if [ "$_dir_dev" = "$_root_dev" ]; then
            chown bond-agent:bond-agent "$dir" 2>/dev/null || true
        fi
    fi
done

# Copy git config to bond-agent user
cp /root/.gitconfig /home/bond-agent/.gitconfig 2>/dev/null || true
chown bond-agent:bond-agent /home/bond-agent/.gitconfig 2>/dev/null || true

su -c "git config --global --add safe.directory /bond" bond-agent
su -c "git config --global --add safe.directory /workspace" bond-agent

# Drop privileges and exec worker
exec gosu bond-agent python -m backend.app.worker "$@"
