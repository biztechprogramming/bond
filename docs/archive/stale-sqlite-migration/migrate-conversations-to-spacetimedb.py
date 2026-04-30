#!/usr/bin/env python3
"""Migrate conversations and messages from SQLite (knowledge.db) to SpacetimeDB.

Usage:
    cd ~/bond && uv run python scripts/migrate-conversations-to-spacetimedb.py

Reads from: ~/.bond/data/knowledge.db
Writes to:  SpacetimeDB bond-core module via HTTP API (localhost:18787)
"""

import json
import sqlite3
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

SQLITE_DB = Path.home() / ".bond" / "data" / "knowledge.db"
SPACETIMEDB_URL = "http://localhost:18787"
MODULE_NAME = "bond-core"


def call_reducer(reducer: str, args: list) -> bool:
    """Call a SpacetimeDB reducer via HTTP API."""
    url = f"{SPACETIMEDB_URL}/v1/database/{MODULE_NAME}/call/{reducer}"
    data = json.dumps(args).encode()
    req = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req) as resp:
            return resp.status == 200
    except HTTPError as e:
        print(f"  ERROR calling {reducer}: {e.status} {e.read().decode()[:200]}")
        return False


def check_spacetimedb() -> bool:
    """Verify SpacetimeDB is running."""
    try:
        req = Request(f"{SPACETIMEDB_URL}/v1/health")
        with urlopen(req) as resp:
            health = json.loads(resp.read())
            print(f"SpacetimeDB: v{health['version']} ✓")
            return True
    except Exception as e:
        print(f"SpacetimeDB unreachable at {SPACETIMEDB_URL}: {e}")
        return False


def ts_to_epoch_ms(ts_str: str | None) -> int:
    """Convert SQLite timestamp string to epoch milliseconds."""
    if not ts_str:
        return int(time.time() * 1000)
    try:
        from datetime import datetime
        # Try ISO format first
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except (ValueError, AttributeError):
        return int(time.time() * 1000)


def migrate():
    if not SQLITE_DB.exists():
        print(f"SQLite DB not found: {SQLITE_DB}")
        sys.exit(1)

    if not check_spacetimedb():
        sys.exit(1)

    conn = sqlite3.connect(str(SQLITE_DB))
    conn.row_factory = sqlite3.Row

    # ── Migrate Conversations ──
    convs = conn.execute(
        "SELECT id, agent_id, channel, title, is_active, message_count, "
        "rolling_summary, summary_covers_to, recent_tools_used, created_at, updated_at "
        "FROM conversations ORDER BY created_at"
    ).fetchall()

    print(f"\nMigrating {len(convs)} conversations...")
    conv_ok = 0
    for c in convs:
        args = [
            c["id"],
            c["agent_id"],
            c["channel"] or "webchat",
            c["title"] or "",
            bool(c["is_active"]),
            c["message_count"] or 0,
            c["rolling_summary"] or "",
            c["summary_covers_to"] or 0,
            c["recent_tools_used"] or "[]",
            ts_to_epoch_ms(c["created_at"]),
            ts_to_epoch_ms(c["updated_at"]),
        ]
        if call_reducer("import_conversation", args):
            conv_ok += 1
            print(f"  ✓ {c['id']} — {c['title'] or '(untitled)'}")
        else:
            print(f"  ✗ {c['id']} — FAILED")

    # ── Migrate Messages ──
    msgs = conn.execute(
        "SELECT id, conversation_id, role, content, tool_calls, tool_call_id, "
        "token_count, status, created_at "
        "FROM conversation_messages ORDER BY created_at"
    ).fetchall()

    print(f"\nMigrating {len(msgs)} messages...")
    msg_ok = 0
    for i, m in enumerate(msgs):
        tool_calls_str = ""
        if m["tool_calls"]:
            tool_calls_str = m["tool_calls"] if isinstance(m["tool_calls"], str) else json.dumps(m["tool_calls"])

        args = [
            m["id"],
            m["conversation_id"],
            m["role"],
            m["content"] or "",
            tool_calls_str,
            m["tool_call_id"] or "",
            m["token_count"] or 0,
            m["status"] or "delivered",
            ts_to_epoch_ms(m["created_at"]),
        ]
        if call_reducer("import_conversation_message", args):
            msg_ok += 1
        else:
            print(f"  ✗ message {m['id']} — FAILED")

        # Progress every 50 messages
        if (i + 1) % 50 == 0:
            print(f"  ... {i + 1}/{len(msgs)} messages migrated")

    conn.close()

    print(f"\n{'='*50}")
    print(f"Conversations: {conv_ok}/{len(convs)} migrated")
    print(f"Messages:      {msg_ok}/{len(msgs)} migrated")

    if conv_ok == len(convs) and msg_ok == len(msgs):
        print("✓ Migration complete — all data transferred")
    else:
        print("⚠ Some records failed — check errors above")
        sys.exit(1)


if __name__ == "__main__":
    migrate()
