#!/usr/bin/env python3
"""Garbage-collect orphaned bond-repo-* docker volumes (Design Doc 113 §4).

Per-agent-per-repo volumes are named `bond-repo-{agent_id}-{repo_id}`. When
an agent or a repo row is deleted from agent_repos, the volume is left
behind so any in-flight clones aren't yanked. This script reaps volumes
whose (agent_id, repo_id) no longer exists in agent_repos.

Usage:
    python3 scripts/gc-bond-repo-volumes.py [--delete] [--stdb-url URL]

Default is dry-run: lists what would be removed. Pass --delete to actually
`docker volume rm` them. Stdlib only.

Exits 0 if everything was either kept or removed cleanly. Non-zero if any
docker / STDB call failed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from urllib.error import HTTPError, URLError


def stdb_sql(stdb_url: str, sql: str) -> list[dict]:
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

    if not payload:
        return []
    block = payload[0]
    columns = [
        el.get("name", {}).get("some")
        for el in block.get("schema", {}).get("elements", [])
    ]
    return [dict(zip(columns, row)) for row in block.get("rows", [])]


def list_bond_repo_volumes() -> list[str]:
    """Return all docker volume names starting with `bond-repo-`."""
    res = subprocess.run(
        ["docker", "volume", "ls", "--filter", "name=^bond-repo-", "--format", "{{.Name}}"],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"docker volume ls failed: {res.stderr.strip()}")
    return [line for line in res.stdout.splitlines() if line.startswith("bond-repo-")]


def parse_volume_name(name: str) -> tuple[str, str] | None:
    """`bond-repo-{agent_id}-{repo_id}` → (agent_id, repo_id), or None.

    agent_id and repo_id are both 26-char ULID-shaped strings, so the split
    is unambiguous: take the trailing 26 chars as repo_id, then strip the
    `-` and trailing 26 chars again for agent_id.
    """
    if not name.startswith("bond-repo-"):
        return None
    rest = name[len("bond-repo-"):]
    if len(rest) < 26 + 1 + 26:
        return None
    repo_id = rest[-26:]
    if rest[-27] != "-":
        return None
    agent_id = rest[:-27]
    if len(agent_id) != 26:
        # Some agent IDs are non-ULID (e.g. "01JBOND...DEFAULT" is 26 chars
        # so this still matches) but be permissive — we only need it to
        # round-trip with what STDB stores.
        pass
    return agent_id, repo_id


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument(
        "--delete", action="store_true",
        help="Actually remove orphaned volumes. Default: dry-run.",
    )
    p.add_argument(
        "--stdb-url",
        default=os.environ.get("BOND_SPACETIMEDB_URL", "http://localhost:18787"),
    )
    args = p.parse_args()

    print(f"STDB: {args.stdb_url}")
    print(f"Mode: {'LIVE (will remove)' if args.delete else 'DRY RUN'}")
    print()

    try:
        volumes = list_bond_repo_volumes()
    except RuntimeError as e:
        print(f"docker error: {e}", file=sys.stderr)
        return 1

    if not volumes:
        print("No bond-repo-* volumes present. Nothing to do.")
        return 0

    try:
        rows = stdb_sql(args.stdb_url, "SELECT id, agent_id FROM agent_repos")
    except RuntimeError as e:
        print(f"STDB error: {e}", file=sys.stderr)
        return 1

    live: set[tuple[str, str]] = {(r["agent_id"], r["id"]) for r in rows}

    counts = {"kept": 0, "orphaned": 0, "removed": 0, "failed": 0, "unparseable": 0}

    for vol in volumes:
        parsed = parse_volume_name(vol)
        if parsed is None:
            print(f"  SKIP (unparseable name): {vol}")
            counts["unparseable"] += 1
            continue

        if parsed in live:
            counts["kept"] += 1
            continue

        agent_id, repo_id = parsed
        counts["orphaned"] += 1
        print(f"  ORPHAN: {vol}  (agent={agent_id} repo={repo_id})")

        if not args.delete:
            continue

        rm = subprocess.run(
            ["docker", "volume", "rm", vol], capture_output=True, text=True,
        )
        if rm.returncode == 0:
            counts["removed"] += 1
            print(f"    removed.")
        else:
            counts["failed"] += 1
            print(f"    FAILED: {rm.stderr.strip()}", file=sys.stderr)

    print()
    print(
        f"Summary: kept={counts['kept']} "
        f"orphaned={counts['orphaned']} "
        f"removed={counts['removed']} "
        f"failed={counts['failed']} "
        f"unparseable={counts['unparseable']}"
    )
    if not args.delete and counts["orphaned"]:
        print("\nRe-run with --delete to remove the orphans listed above.")

    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
