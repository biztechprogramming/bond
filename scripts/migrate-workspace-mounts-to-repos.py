#!/usr/bin/env python3
"""Migrate agent_workspace_mounts rows to agent_repos (Design Doc 113 §6).

Walks every workspace_mount row, classifies the host_path:

    git-with-remote → creates an agent_repos row from the remote URL +
                       current branch; (with --delete) removes the mount.
    git-no-remote   → prints a warning, leaves the mount alone.
    not-a-git-repo  → prints a warning, leaves the mount alone.
    missing-path    → prints a warning, leaves the mount alone.

Idempotent: re-running skips repos already migrated (matched by agent_id +
mount_name).

Usage:
    python3 scripts/migrate-workspace-mounts-to-repos.py [--dry-run] [--delete]
        [--stdb-url URL]

Exits 0 if every mount was either migrated or warned-about cleanly. Exits
non-zero if a query fails. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from urllib.error import HTTPError, URLError


# Borrowed from backend's ulid usage. We don't need full ULID semantics —
# any monotonic, sortable, 26-char string works for new IDs. This avoids
# needing the python-ulid dependency on a host script.
import secrets


def _make_id() -> str:
    """26-char base32 ID. Not a real ULID but interchangeable for STDB use."""
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    ms = int(time.time() * 1000)
    rand_bytes = secrets.token_bytes(10)
    # Encode 48-bit timestamp + 80-bit randomness into 26 base32 chars
    n = (ms << 80) | int.from_bytes(rand_bytes, "big")
    out = []
    for _ in range(26):
        out.append(alphabet[n & 0x1F])
        n >>= 5
    return "".join(reversed(out))


def stdb_sql(stdb_url: str, sql: str) -> list[dict]:
    """Execute SQL against SpacetimeDB and return rows as dicts."""
    req = urllib.request.Request(
        f"{stdb_url}/v1/database/bond-core-v2/sql",
        data=sql.encode(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read())
    except HTTPError as e:
        raise RuntimeError(f"STDB HTTP {e.code}: {e.read().decode()}") from e
    except URLError as e:
        raise RuntimeError(f"STDB unreachable at {stdb_url}: {e}") from e

    # STDB returns a list of result blocks; for a single statement, take [0].
    if not payload:
        return []
    block = payload[0]
    columns = [el.get("name", {}).get("some") for el in block.get("schema", {}).get("elements", [])]
    return [dict(zip(columns, row)) for row in block.get("rows", [])]


def classify_path(path: str) -> tuple[str, dict]:
    """Return (status, info) for a host path.

    status ∈ {git-remote, git-no-remote, not-git, missing}
    info has keys: url (when git-remote), branch (when git-remote), error (when fail)
    """
    expanded = os.path.expanduser(path)
    if not os.path.isdir(expanded):
        return ("missing", {"path": expanded})

    is_repo = subprocess.run(
        ["git", "-C", expanded, "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True,
    )
    if is_repo.returncode != 0 or is_repo.stdout.strip() != "true":
        return ("not-git", {"path": expanded})

    remote = subprocess.run(
        ["git", "-C", expanded, "remote", "get-url", "origin"],
        capture_output=True, text=True,
    )
    if remote.returncode != 0 or not remote.stdout.strip():
        return ("git-no-remote", {"path": expanded})

    branch = subprocess.run(
        ["git", "-C", expanded, "branch", "--show-current"],
        capture_output=True, text=True,
    )
    return (
        "git-remote",
        {
            "path": expanded,
            "url": remote.stdout.strip(),
            "branch": branch.stdout.strip() or "main",
        },
    )


def _escape_sql(value: str) -> str:
    return value.replace("'", "''")


def already_migrated(stdb_url: str, agent_id: str, mount_name: str) -> bool:
    rows = stdb_sql(
        stdb_url,
        f"SELECT id FROM agent_repos WHERE agent_id = '{_escape_sql(agent_id)}' "
        f"AND name = '{_escape_sql(mount_name)}'",
    )
    return bool(rows)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--dry-run", action="store_true", help="Report what would be done; make no changes.")
    p.add_argument(
        "--delete", action="store_true",
        help="Delete migrated workspace_mount rows. Default: leave them in place.",
    )
    p.add_argument(
        "--stdb-url", default=os.environ.get("BOND_SPACETIMEDB_URL", "http://localhost:18787"),
    )
    args = p.parse_args()

    print(f"STDB:     {args.stdb_url}")
    print(f"Mode:     {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"Delete:   {'yes' if args.delete else 'no'}")
    print()

    try:
        mounts = stdb_sql(
            args.stdb_url,
            "SELECT id, agent_id, host_path, mount_name, container_path, readonly "
            "FROM agent_workspace_mounts",
        )
    except RuntimeError as e:
        print(f"Failed to fetch mounts: {e}", file=sys.stderr)
        return 1

    if not mounts:
        print("No workspace_mounts to process.")
        return 0

    counts = {"migrated": 0, "skipped": 0, "warn": 0, "deleted": 0}
    for m in mounts:
        agent_id = m["agent_id"]
        mount_name = m["mount_name"]
        host_path = m["host_path"]
        prefix = f"  agent={agent_id} mount={mount_name}"

        status, info = classify_path(host_path)

        if status != "git-remote":
            reason = {
                "missing": f"path does not exist on host: {info['path']}",
                "not-git": f"not a git repo: {info['path']}",
                "git-no-remote": f"git repo has no 'origin' remote: {info['path']}",
            }[status]
            print(f"{prefix} → SKIP — {reason}")
            counts["warn"] += 1
            continue

        if already_migrated(args.stdb_url, agent_id, mount_name):
            print(f"{prefix} → already migrated — skipping")
            counts["skipped"] += 1
            continue

        if args.dry_run:
            print(
                f"{prefix} → WOULD CREATE agent_repos: "
                f"url={info['url']} branch={info['branch']}"
            )
            counts["migrated"] += 1
            continue

        repo_id = _make_id()
        now = int(time.time() * 1000)
        try:
            stdb_sql(
                args.stdb_url,
                "INSERT INTO agent_repos ("
                "id, agent_id, url, name, default_branch, active_branch, "
                "credential_id, last_synced_at, created_at, updated_at"
                ") VALUES ("
                f"'{repo_id}',"
                f"'{_escape_sql(agent_id)}',"
                f"'{_escape_sql(info['url'])}',"
                f"'{_escape_sql(mount_name)}',"
                f"'{_escape_sql(info['branch'])}',"
                f"'',"
                f"'',"
                f"0, {now}, {now}"
                ")",
            )
            print(
                f"{prefix} → migrated id={repo_id} url={info['url']} branch={info['branch']}"
            )
            counts["migrated"] += 1

            if args.delete:
                stdb_sql(
                    args.stdb_url,
                    f"DELETE FROM agent_workspace_mounts WHERE id = '{_escape_sql(m['id'])}'",
                )
                counts["deleted"] += 1
        except RuntimeError as e:
            print(f"{prefix} → FAILED: {e}", file=sys.stderr)
            counts["warn"] += 1

    print()
    print(
        f"Summary: migrated={counts['migrated']} "
        f"skipped={counts['skipped']} "
        f"warned={counts['warn']} "
        f"deleted={counts['deleted']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
