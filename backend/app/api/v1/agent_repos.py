"""Agent repos API (Design Doc 113).

Per-agent git repo configuration. Replaces bind-mounted host directories with
git clones into Docker volumes. Endpoints live under /agents/{agent_id}/repos
to mirror the existing /agents/{agent_id} sub-resource pattern (channels,
mounts, etc.).
"""

from __future__ import annotations

import logging
import re
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from ulid import ULID

from backend.app.core.spacetimedb import get_stdb

logger = logging.getLogger("bond.api.agent_repos")

router = APIRouter(prefix="/agents", tags=["agent-repos"])


# ── Pydantic models ──────────────────────────────────────────


class RepoCreate(BaseModel):
    url: str = Field(..., min_length=1)
    name: str = ""  # auto-derived from URL basename if empty
    default_branch: str = "main"
    active_branch: str = ""  # empty = use default_branch on first run
    credential_id: str = ""  # empty = resolve by host_pattern fallback


class RepoUpdate(BaseModel):
    url: str | None = None
    name: str | None = None
    default_branch: str | None = None
    active_branch: str | None = None
    credential_id: str | None = None


# ── Helpers ───────────────────────────────────────────────────


def _escape_sql(value):
    if value is None:
        return ""
    return str(value).replace("'", "''")


_BASENAME_RE = re.compile(r"[^/:]+?(?:\.git)?/?\s*$")


def derive_repo_name(url: str) -> str:
    """Extract a sensible mount name from a clone URL.

    Examples:
        git@github.com:foo/bar.git → bar
        https://github.com/foo/bar → bar
        https://dev.azure.com/org/proj/_git/myrepo → myrepo
    """
    url = url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    # take the last path segment
    for sep in ("/", ":"):
        if sep in url:
            url = url.rsplit(sep, 1)[-1]
    return url or "repo"


_MOUNT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _validate_mount_name(name: str) -> None:
    if not _MOUNT_NAME_RE.match(name):
        raise HTTPException(
            400,
            "name must start alphanumeric and contain only letters, numbers, underscores, dots, or hyphens",
        )


async def _agent_exists(agent_id: str) -> bool:
    stdb = get_stdb()
    rows = await stdb.query(f"SELECT id FROM agents WHERE id = '{agent_id}'")
    return bool(rows)


def _row_to_dict(row: dict) -> dict:
    """Map an STDB row to API response shape.

    Note: SpacetimeDB SQL returns column names in snake_case (the schema
    declares them in camelCase but the SQL layer normalizes).
    """
    return {
        "id": row["id"],
        "agent_id": row["agent_id"],
        "url": row["url"],
        "name": row["name"],
        "default_branch": row["default_branch"],
        "active_branch": row["active_branch"] or "",
        "credential_id": row["credential_id"] or "",
        "last_synced_at": int(row["last_synced_at"]) if row.get("last_synced_at") else 0,
        "created_at": int(row.get("created_at") or 0),
        "updated_at": int(row.get("updated_at") or row.get("created_at") or 0),
    }


# ── Endpoints ─────────────────────────────────────────────────


@router.get("/{agent_id}/repos")
async def list_repos(agent_id: str):
    if not await _agent_exists(agent_id):
        raise HTTPException(404, "Agent not found")
    stdb = get_stdb()
    rows = await stdb.query(
        f"SELECT * FROM agent_repos WHERE agent_id = '{agent_id}'"
    )
    rows.sort(key=lambda r: r["name"].lower())
    return [_row_to_dict(r) for r in rows]


@router.post("/{agent_id}/repos")
async def create_repo(agent_id: str, body: RepoCreate):
    if not await _agent_exists(agent_id):
        raise HTTPException(404, "Agent not found")

    stdb = get_stdb()
    name = body.name.strip() or derive_repo_name(body.url)
    _validate_mount_name(name)

    # Reject duplicate mount names within an agent — they'd collide on /workspace/{name}
    existing = await stdb.query(
        f"SELECT id FROM agent_repos WHERE agent_id = '{agent_id}' AND name = '{_escape_sql(name)}'"
    )
    if existing:
        raise HTTPException(409, f"Agent already has a repo named '{name}'")

    if body.credential_id:
        cred = await stdb.query(
            f"SELECT id FROM git_credentials WHERE id = '{body.credential_id}'"
        )
        if not cred:
            raise HTTPException(400, f"credential_id {body.credential_id} does not exist")

    repo_id = str(ULID())
    now = int(time.time() * 1000)

    await stdb.query(f"""
        INSERT INTO agent_repos (
            id, agent_id, url, name, default_branch, active_branch, credential_id,
            last_synced_at, created_at, updated_at
        ) VALUES (
            '{repo_id}',
            '{agent_id}',
            '{_escape_sql(body.url)}',
            '{_escape_sql(name)}',
            '{_escape_sql(body.default_branch or "main")}',
            '{_escape_sql(body.active_branch)}',
            '{_escape_sql(body.credential_id)}',
            0,
            {now},
            {now}
        )
    """)

    rows = await stdb.query(f"SELECT * FROM agent_repos WHERE id = '{repo_id}'")
    return _row_to_dict(rows[0])


@router.put("/{agent_id}/repos/{repo_id}")
async def update_repo(agent_id: str, repo_id: str, body: RepoUpdate):
    stdb = get_stdb()
    rows = await stdb.query(
        f"SELECT * FROM agent_repos WHERE id = '{repo_id}' AND agent_id = '{agent_id}'"
    )
    if not rows:
        raise HTTPException(404, "Repo not found for this agent")

    if body.credential_id:
        cred = await stdb.query(
            f"SELECT id FROM git_credentials WHERE id = '{body.credential_id}'"
        )
        if not cred:
            raise HTTPException(400, f"credential_id {body.credential_id} does not exist")

    updates = []
    if body.url is not None:
        updates.append(f"url = '{_escape_sql(body.url)}'")
    if body.name is not None:
        new_name = body.name.strip() or rows[0]["name"]
        _validate_mount_name(new_name)
        # Don't collide with another repo on the same agent
        clash = await stdb.query(
            f"SELECT id FROM agent_repos WHERE agent_id = '{agent_id}' "
            f"AND name = '{_escape_sql(new_name)}' AND id != '{repo_id}'"
        )
        if clash:
            raise HTTPException(409, f"Agent already has a repo named '{new_name}'")
        updates.append(f"name = '{_escape_sql(new_name)}'")
    if body.default_branch is not None:
        updates.append(f"default_branch = '{_escape_sql(body.default_branch)}'")
    if body.active_branch is not None:
        updates.append(f"active_branch = '{_escape_sql(body.active_branch)}'")
    if body.credential_id is not None:
        updates.append(f"credential_id = '{_escape_sql(body.credential_id)}'")

    if updates:
        updates.append(f"updated_at = {int(time.time() * 1000)}")
        await stdb.query(
            f"UPDATE agent_repos SET {', '.join(updates)} WHERE id = '{repo_id}'"
        )

    rows = await stdb.query(f"SELECT * FROM agent_repos WHERE id = '{repo_id}'")
    return _row_to_dict(rows[0])


@router.delete("/{agent_id}/repos/{repo_id}")
async def delete_repo(agent_id: str, repo_id: str):
    stdb = get_stdb()
    rows = await stdb.query(
        f"SELECT id FROM agent_repos WHERE id = '{repo_id}' AND agent_id = '{agent_id}'"
    )
    if not rows:
        raise HTTPException(404, "Repo not found for this agent")

    await stdb.query(f"DELETE FROM agent_repos WHERE id = '{repo_id}'")
    # Volume cleanup is deferred — a future GC sweep will reap orphaned bond-repo-* volumes.
    return {"success": True}
