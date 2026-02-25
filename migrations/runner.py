"""Migration runner — applies SQL migrations in order.

Tracks applied migrations in a `_migrations` table.
Usage:
    python -m migrations.runner [--db PATH] [up|down|status]
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent


def _get_migration_files(direction: str = "up") -> list[tuple[str, Path]]:
    """Return sorted list of (version, path) for migration files."""
    pattern = re.compile(r"^(\d+)_.+\." + direction + r"\.sql$")
    files = []
    for f in sorted(MIGRATIONS_DIR.iterdir()):
        m = pattern.match(f.name)
        if m:
            files.append((m.group(1), f))
    return files


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            version TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
        )
    """)
    conn.commit()


def _applied_versions(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT version FROM _migrations ORDER BY version").fetchall()
    return {r[0] for r in rows}


def migrate_up(db_path: str) -> None:
    """Apply all pending up migrations."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_migrations_table(conn)
    applied = _applied_versions(conn)
    files = _get_migration_files("up")

    pending = [(v, p) for v, p in files if v not in applied]
    if not pending:
        print("Nothing to migrate — all up to date.")
        return

    for version, path in pending:
        print(f"Applying {path.name} ...", end=" ")
        sql = path.read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO _migrations (version, name) VALUES (?, ?)",
            (version, path.stem),
        )
        conn.commit()
        print("OK")

    print(f"Applied {len(pending)} migration(s).")


def migrate_down(db_path: str, steps: int = 1) -> None:
    """Roll back the last N migrations."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_migrations_table(conn)
    applied = sorted(_applied_versions(conn), reverse=True)
    down_files = {v: p for v, p in _get_migration_files("down")}

    rolled = 0
    for version in applied:
        if rolled >= steps:
            break
        path = down_files.get(version)
        if not path:
            print(f"No down migration for {version}, skipping.")
            continue
        print(f"Rolling back {path.name} ...", end=" ")
        sql = path.read_text()
        conn.executescript(sql)
        conn.execute("DELETE FROM _migrations WHERE version = ?", (version,))
        conn.commit()
        print("OK")
        rolled += 1

    print(f"Rolled back {rolled} migration(s).")


def migrate_status(db_path: str) -> None:
    """Show migration status."""
    conn = sqlite3.connect(db_path)
    _ensure_migrations_table(conn)
    applied = _applied_versions(conn)
    files = _get_migration_files("up")

    for version, path in files:
        status = "✓" if version in applied else "✗"
        print(f"  {status}  {path.name}")

    pending = [v for v, _ in files if v not in applied]
    if pending:
        print(f"\n{len(pending)} pending migration(s). Run 'make migrate' to apply.")
    else:
        print("\nAll migrations applied.")


def main():
    parser = argparse.ArgumentParser(description="Bond migration runner")
    parser.add_argument("action", nargs="?", default="up", choices=["up", "down", "status"])
    parser.add_argument("--db", default=None, help="Path to SQLite database")
    parser.add_argument("--steps", type=int, default=1, help="Number of migrations to roll back (down only)")
    args = parser.parse_args()

    if args.db:
        db_path = args.db
    else:
        # Try to read from bond.json config
        import json
        import os
        bond_home = Path(os.environ.get("BOND_HOME", Path.home() / ".bond"))
        bond_json = bond_home / "bond.json"
        if bond_json.exists():
            config = json.loads(bond_json.read_text())
            db_path = config.get("database", {}).get("path", str(bond_home / "data" / "knowledge.db"))
        else:
            db_path = str(bond_home / "data" / "knowledge.db")

    # Ensure parent dir exists
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    if args.action == "up":
        migrate_up(db_path)
    elif args.action == "down":
        migrate_down(db_path, steps=args.steps)
    elif args.action == "status":
        migrate_status(db_path)


if __name__ == "__main__":
    main()
