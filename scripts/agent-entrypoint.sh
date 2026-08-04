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
        ssh-keyscan -H github.com ssh.dev.azure.com >> ~/.ssh/known_hosts 2>/dev/null || true
    fi
fi

# If the mounted SSH key authenticates to github.com, rewrite all github.com
# HTTPS URLs to SSH form. Per-repo HTTPS configs (or missing/expired PATs) can
# no longer silently shadow the agent's working SSH credentials.
# Probe github SSH auth ONCE here and reuse the result for the bond-agent
# gitconfig mirror below — the probe is a network round-trip (~1s) and running
# it twice per boot was pure cold-start tax.
GITHUB_SSH_OK=0
if ssh -o BatchMode=yes -o ConnectTimeout=5 -T git@github.com 2>&1 | grep -q "successfully authenticated"; then
    GITHUB_SSH_OK=1
    git config --global url."git@github.com:".insteadOf "https://github.com/"
    echo "[entrypoint] github.com SSH verified; HTTPS URLs rewritten to SSH."
fi

# Use the bond repo at /bond.
# Dev mode (BOND_DEV_SKIP_GIT): /bond is a bind-mount of the developer's host
# working tree. Skip all git operations — a `git reset --hard` here would wipe
# their uncommitted edits, and a fetch is pointless since the mount is already
# live. The host-side adapter sets this only when BOND_DEV_MOUNT_SOURCE is on.
if [ "${BOND_DEV_SKIP_GIT:-}" = "1" ]; then
    echo "[entrypoint] Dev mode: /bond is a host mount — skipping git clone/fetch/reset."
# If not present, clone fresh. Always pull latest on startup.
elif [ ! -d "/bond/.git" ]; then
    echo "[entrypoint] Cloning bond repo..."
    git clone "${BOND_REPO_URL:-git@github.com:biztechprogramming/bond.git}" /bond
    echo "[entrypoint] Clone complete."
    # Design Doc 116 §3.3: honor a previously chosen branch on fresh clone
    # so a docker-recreate (volume wipe) doesn't drop the user's selection.
    if [ -f /data/bond-branch ]; then
        SAVED_BRANCH=$(cat /data/bond-branch | tr -d '[:space:]')
        if [ -n "$SAVED_BRANCH" ]; then
            echo "[entrypoint] Restoring saved branch: $SAVED_BRANCH"
            (cd /bond && git checkout "$SAVED_BRANCH" 2>/dev/null) || \
                echo "[entrypoint] WARN: could not checkout saved branch '$SAVED_BRANCH'"
        fi
    fi
else
    CURRENT_BRANCH=$(cd /bond && git branch --show-current 2>/dev/null || echo "unknown")
    echo "[entrypoint] Using bond repo (branch: $CURRENT_BRANCH) — pulling latest..."
    cd /bond && git fetch origin && git reset --hard "origin/$CURRENT_BRANCH" 2>/dev/null || true
    echo "[entrypoint] Pull complete."
fi

# Check out the vendored skill submodules (Design Doc 047). A plain `git clone`
# / `git reset` leaves vendor/skills/* as empty gitlinks, which silently breaks
# L2 skill activation: the skills tool reads SKILL.md from
# /bond/vendor/skills/<repo>/... (cwd is /bond), so an empty checkout makes
# every vendored skill un-activatable. --init registers the submodules first
# (they are absent from a fresh clone's .git/config); we check out the pinned
# commit here (not --remote) — advancing to upstream latest is the daily
# sync_skills job's responsibility, not the cold-start path.
if [ "${BOND_DEV_SKIP_GIT:-}" != "1" ] && [ -d /bond/.git ]; then
    echo "[entrypoint] Initializing skill submodules..."
    (cd /bond && git submodule update --init --recursive vendor/skills/) || \
        echo "[entrypoint] WARN: skill submodule init failed — vendored skills may be unavailable."
fi

# --- Agent repos (Design Doc 113) ---
# Read /config/repos.json (written by the host adapter) and clone each repo
# into /workspace/{name}, fetching on subsequent runs. Supports both HTTPS
# PAT (via ~/.git-credentials store) and SSH key (per-repo key file +
# core.sshCommand on the local repo) auth.
if [ -f /config/repos.json ]; then
    echo "[entrypoint] Processing agent_repos config..."
    mkdir -p /workspace
    HOME_DIR="${HOME:-/root}"

    if ! python3 - "$HOME_DIR" <<'PYEOF'
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, urlparse

home = Path(sys.argv[1])
cfg = json.loads(Path("/config/repos.json").read_text())
repos = cfg.get("repos", [])

# ── HTTPS PAT setup ────────────────────────────────────────────
# One git-credentials store covers all HTTPS repos; git matches by URL.
https_repos = [r for r in repos if (r.get("credential") or {}).get("auth_type") == "https_pat"]
if https_repos:
    subprocess.run(["git", "config", "--global", "credential.helper", "store"], check=True)
    creds_path = home / ".git-credentials"
    lines = []
    for r in https_repos:
        cred = r["credential"]
        secret = cred.get("secret") or ""
        if not secret:
            continue
        url = r.get("url", "")
        parsed = urlparse(url if url.startswith(("http://", "https://")) else f"https://{url}")
        if not parsed.hostname:
            continue
        # Prefer credential-configured username, then URL-embedded username, then fallback.
        # Azure DevOps URLs embed the org name as userinfo (alliedim@dev.azure.com);
        # the .git-credentials entry must use the same username for git to match it.
        username = cred.get("username") or parsed.username or "x-access-token"
        host = parsed.hostname
        if parsed.port:
            host = f"{host}:{parsed.port}"
        lines.append(f"{parsed.scheme}://{quote(username, safe='')}:{quote(secret, safe='')}@{host}")
    creds_path.write_text("\n".join(lines) + ("\n" if lines else ""))
    creds_path.chmod(0o600)

# ── SSH key setup ──────────────────────────────────────────────
# One key file per repo, written under ~/.ssh/bondrepo_{repo_id}. Using
# `~` in the SSH command so it works under both root (during this script)
# and bond-agent (post-privilege-drop) since the privilege-drop section
# copies /root/.ssh to /home/bond-agent/.ssh.
ssh_dir = home / ".ssh"
ssh_dir.mkdir(mode=0o700, exist_ok=True)


def ssh_command_for(repo_id: str) -> str:
    """Build a GIT_SSH_COMMAND that uses only this repo's key."""
    # accept-new: auto-add unknown hosts to known_hosts on first contact, but
    # reject changed host keys. Right tradeoff for unattended agents.
    return (
        f"ssh -i ~/.ssh/bondrepo_{repo_id} "
        f"-o IdentitiesOnly=yes "
        f"-o StrictHostKeyChecking=accept-new "
        f"-o UserKnownHostsFile=~/.ssh/known_hosts"
    )


for r in repos:
    cred = r.get("credential") or {}
    if cred.get("auth_type") != "ssh_key":
        continue
    secret = cred.get("secret") or ""
    if not secret:
        continue
    repo_id = r.get("id") or ""
    if not repo_id:
        continue
    key_path = ssh_dir / f"bondrepo_{repo_id}"
    # SSH is fussy about key formatting — must end with a newline.
    body = secret if secret.endswith("\n") else secret + "\n"
    key_path.write_text(body)
    key_path.chmod(0o600)

# ── Clone (or fetch) each repo ─────────────────────────────────
exit_code = 0
for r in repos:
    name = r.get("name") or ""
    url = r.get("url") or ""
    repo_id = r.get("id") or ""
    if not name or not url:
        continue
    dest = Path("/workspace") / name
    default_branch = r.get("default_branch") or "main"
    active_branch = r.get("active_branch") or ""
    cred = r.get("credential") or {}
    is_ssh = cred.get("auth_type") == "ssh_key" and bool(cred.get("secret")) and bool(repo_id)

    subprocess.run(
        ["git", "config", "--global", "--add", "safe.directory", str(dest)],
        check=False,
    )

    env = os.environ.copy()
    if is_ssh:
        env["GIT_SSH_COMMAND"] = ssh_command_for(repo_id)

    if (dest / ".git").is_dir():
        print(f"[entrypoint] Repo '{name}' already cloned — fetching...")
        rc = subprocess.run(["git", "fetch", "--all", "--prune"], cwd=dest, env=env).returncode
        if rc != 0:
            print(f"[entrypoint] WARN: fetch failed for {name}")
        # Branch switching for an already-cloned repo is owned by the worker, not
        # the entrypoint (Design Doc 113 §5.2, Doc 119 §A). The worker runs
        # _reconcile_repo_branches() at each turn: it switches only when the tree
        # is clean and, when there's uncommitted work, leaves the repo untouched
        # and asks the user in chat how to proceed. A naive `git checkout` here
        # aborts on a dirty tree ("Your local changes would be overwritten") and
        # — being block-buffered behind that git stderr — made cold starts look
        # wedged. So we deliberately do NOT switch branches here anymore.
    else:
        branch = active_branch or default_branch
        print(f"[entrypoint] Cloning {name} ({url}) into {dest}...")
        rc = subprocess.run(
            ["git", "clone", "--branch", branch, url, str(dest)], env=env,
        ).returncode
        if rc != 0:
            # Branch didn't exist on remote — clone default and try to switch
            rc = subprocess.run(["git", "clone", url, str(dest)], env=env).returncode
            if rc != 0:
                print(f"[entrypoint] ERROR: clone failed for {name}")
                exit_code = 1
                continue
            if active_branch:
                subprocess.run(["git", "checkout", active_branch], cwd=dest, env=env)

    # Persist the SSH command on the local repo so post-clone fetches and
    # pushes (run by bond-agent) keep using the right key without env vars.
    if is_ssh:
        subprocess.run(
            ["git", "config", "core.sshCommand", ssh_command_for(repo_id)],
            cwd=dest, check=False,
        )

    # Chown the cloned repo to bond-agent so the worker can write to it
    # after privilege drop. Volumes are docker-managed so this is safe
    # (no host-side ownership impact).
    subprocess.run(["chown", "-R", "bond-agent:bond-agent", str(dest)], check=False)

sys.exit(exit_code)
PYEOF
    then
        echo "[entrypoint] ERROR: one or more repo clones failed; refusing to start agent."
        exit 1
    fi

    echo "[entrypoint] agent_repos processing complete."
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

# Copy git credentials store (Design Doc 113) so bond-agent can fetch/push
# from cloned repos after privilege drop.
if [ -f /root/.git-credentials ]; then
    cp /root/.git-credentials /home/bond-agent/.git-credentials
    chown bond-agent:bond-agent /home/bond-agent/.git-credentials
    chmod 600 /home/bond-agent/.git-credentials
fi

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

# Mirror the github.com HTTPS→SSH rewrite into bond-agent's gitconfig so it
# applies after privilege drop (the worker runs as bond-agent, not root).
if ! grep -q 'insteadOf = https://github.com/' "$BOND_AGENT_GITCONFIG" 2>/dev/null; then
    if [ "$GITHUB_SSH_OK" = "1" ]; then
        cat >> "$BOND_AGENT_GITCONFIG" <<'EOF'
[url "git@github.com:"]
	insteadOf = https://github.com/
EOF
        chown bond-agent:bond-agent "$BOND_AGENT_GITCONFIG" 2>/dev/null || true
    fi
fi

# Drop privileges and exec worker as bond-agent
exec gosu bond-agent python -m backend.app.worker "$@"
