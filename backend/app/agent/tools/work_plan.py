"""Work plan tool — create and manage structured task plans.

Agents use this to track multi-step tasks with context checkpointing
for crash recovery and user visibility via the Task Board UI.
"""

from __future__ import annotations

import json
import os
import logging
from datetime import datetime, timezone
from typing import Any

import aiosqlite
from ulid import ULID

logger = logging.getLogger("bond.agent.tools.work_plan")

# When running in a container, use the host API instead of direct DB access.
# The container gets BOND_API_URL set by the sandbox manager.
_BOND_API_URL = os.environ.get("BOND_API_URL", "")

def _use_api() -> bool:
    """Return True if we should use the host API instead of direct DB."""
    return bool(_BOND_API_URL)

# Plans live in the shared database so they're visible across agents and the API.
# Inside container: /bond-home/data/knowledge.db; on host: ~/.bond/data/knowledge.db
_SHARED_PLANS_DB = (
    "/bond-home/data/knowledge.db"
    if os.path.exists("/bond-home/data/knowledge.db")
    else os.path.expanduser("~/.bond/data/knowledge.db")
)

_PLANS_SCHEMA = """
CREATE TABLE IF NOT EXISTS work_plans (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    conversation_id TEXT,
    parent_plan_id TEXT,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS work_items (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES work_plans(id),
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    ordinal INTEGER NOT NULL DEFAULT 0,
    context_snapshot TEXT,
    notes TEXT DEFAULT '[]',
    files_changed TEXT DEFAULT '[]',
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

async def _get_plans_db() -> aiosqlite.Connection:
    """Get a connection to the shared plans database."""
    os.makedirs(os.path.dirname(_SHARED_PLANS_DB), exist_ok=True)
    db = await aiosqlite.connect(_SHARED_PLANS_DB)
    await db.executescript(_PLANS_SCHEMA)
    return db

# Terminal statuses for work items
_TERMINAL_ITEM_STATUSES = frozenset({"done", "complete", "failed"})

# Terminal statuses for plans
_TERMINAL_PLAN_STATUSES = frozenset({"completed", "failed", "cancelled"})

# Valid status transitions for work items
_VALID_ITEM_STATUSES = frozenset({
    "new", "in_progress", "done", "in_review", "approved",
    "in_test", "tested", "complete", "blocked", "failed",
})

# Valid plan statuses
_VALID_PLAN_STATUSES = frozenset({"active", "paused", "completed", "failed", "cancelled"})


async def handle_work_plan(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Manage work plans and items.

    Actions: create_plan, add_item, update_item, complete_plan, get_plan.
    """
    action = arguments.get("action", "")
    agent_id: str = context.get("agent_id", "unknown")

    if not action:
        return {"error": "action is required"}

    if _use_api():
        return await _handle_via_api(action, arguments, agent_id, context)

    db = await _get_plans_db()
    try:
        if action == "create_plan":
            return await _create_plan(arguments, db, agent_id, context)
        elif action == "add_item":
            return await _add_item(arguments, db, context)
        elif action == "update_item":
            return await _update_item(arguments, db, context)
        elif action == "complete_plan":
            return await _complete_plan(arguments, db, context)
        elif action == "get_plan":
            return await _get_plan(arguments, db)
        else:
            return {"error": f"Unknown action: {action}. Valid: create_plan, add_item, update_item, complete_plan, get_plan"}
    except Exception as e:
        logger.warning("work_plan action=%s failed: %s", action, e, exc_info=True)
        return {"error": str(e)}
    finally:
        await db.close()


async def _handle_via_api(
    action: str,
    arguments: dict[str, Any],
    agent_id: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Route work_plan actions through the host Bond API."""
    import httpx

    base = _BOND_API_URL.rstrip("/")
    url = f"{base}/api/v1/plans"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if action == "create_plan":
                title = arguments.get("title", "")
                if not title:
                    return {"error": "title is required for create_plan"}
                resp = await client.post(url, json={
                    "title": title,
                    "agent_id": agent_id,
                    "conversation_id": context.get("conversation_id", ""),
                    "parent_plan_id": arguments.get("parent_plan_id"),
                })
                resp.raise_for_status()
                result = resp.json()
                result["_sse_event"] = {
                    "event": "plan_created",
                    "data": {"plan_id": result["plan_id"], "title": title, "agent_id": agent_id},
                }
                logger.info("work_plan create_plan (API) id=%s title=%s", result["plan_id"], title[:80])
                return result

            elif action == "add_item":
                plan_id = arguments.get("plan_id", "")
                title = arguments.get("title", "")
                if not plan_id:
                    return {"error": "plan_id is required for add_item"}
                if not title:
                    return {"error": "title is required for add_item"}
                ordinal = arguments.get("ordinal")
                body: dict[str, Any] = {"title": title}
                if ordinal is not None:
                    body["ordinal"] = ordinal
                resp = await client.post(f"{url}/{plan_id}/items", json=body)
                resp.raise_for_status()
                result = resp.json()
                result["_sse_event"] = {
                    "event": "item_created",
                    "data": {"plan_id": plan_id, "item_id": result["item_id"], "title": title, "ordinal": result.get("ordinal")},
                }
                logger.info("work_plan add_item (API) id=%s plan=%s title=%s", result["item_id"], plan_id, title[:80])
                return result

            elif action == "update_item":
                plan_id = arguments.get("plan_id", "")
                item_id = arguments.get("item_id", "")
                if not plan_id or not item_id:
                    return {"error": "plan_id and item_id are required for update_item"}
                body = {}
                if "status" in arguments:
                    body["status"] = arguments["status"]
                if "notes" in arguments:
                    body["notes"] = arguments["notes"]
                if "context_snapshot" in arguments:
                    body["context_snapshot"] = arguments["context_snapshot"]
                if "files_changed" in arguments:
                    body["files_changed"] = arguments["files_changed"]
                resp = await client.put(f"{url}/{plan_id}/items/{item_id}", json=body)
                resp.raise_for_status()
                result = resp.json()
                result["_sse_event"] = {
                    "event": "item_updated",
                    "data": {"plan_id": plan_id, "item_id": item_id, "status": arguments.get("status")},
                }
                logger.info("work_plan update_item (API) item=%s status=%s", item_id, arguments.get("status"))
                return result

            elif action == "complete_plan":
                plan_id = arguments.get("plan_id", "")
                if not plan_id:
                    return {"error": "plan_id is required for complete_plan"}
                status = arguments.get("status", "completed")
                resp = await client.post(f"{url}/{plan_id}/complete", json={"status": status})
                resp.raise_for_status()
                result = resp.json()
                result["_sse_event"] = {
                    "event": "plan_completed",
                    "data": {"plan_id": plan_id, "status": status},
                }
                logger.info("work_plan complete_plan (API) plan=%s status=%s", plan_id, status)
                return result

            elif action == "get_plan":
                plan_id = arguments.get("plan_id", "")
                if not plan_id:
                    return {"error": "plan_id is required for get_plan"}
                resp = await client.get(f"{url}/{plan_id}")
                resp.raise_for_status()
                return resp.json()

            else:
                return {"error": f"Unknown action: {action}. Valid: create_plan, add_item, update_item, complete_plan, get_plan"}

    except httpx.HTTPStatusError as e:
        logger.warning("work_plan API error: %s %s", e.response.status_code, e.response.text[:200])
        return {"error": f"API error: {e.response.status_code} {e.response.text[:200]}"}
    except Exception as e:
        logger.warning("work_plan API call failed: %s", e)
        return {"error": f"API call failed: {e}"}


async def _create_plan(
    arguments: dict[str, Any],
    db: aiosqlite.Connection,
    agent_id: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Create a new work plan."""
    title = arguments.get("title", "")
    if not title:
        return {"error": "title is required for create_plan"}

    plan_id = str(ULID())
    conversation_id = context.get("conversation_id", "")
    parent_plan_id = arguments.get("parent_plan_id")
    now = datetime.now(timezone.utc).isoformat()

    await db.execute(
        "INSERT INTO work_plans (id, agent_id, conversation_id, parent_plan_id, title, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 'active', ?, ?)",
        (plan_id, agent_id, conversation_id, parent_plan_id, title, now, now),
    )
    await db.commit()

    logger.info("work_plan create_plan id=%s title=%s", plan_id, title[:80])

    result: dict[str, Any] = {
        "status": "created",
        "plan_id": plan_id,
        "title": title,
    }

    # Attach SSE event data for the worker to emit
    result["_sse_event"] = {
        "event": "plan_created",
        "data": {"plan_id": plan_id, "title": title, "agent_id": agent_id},
    }

    return result


async def _add_item(
    arguments: dict[str, Any],
    db: aiosqlite.Connection,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Add a work item to a plan."""
    plan_id = arguments.get("plan_id", "")
    title = arguments.get("title", "")

    if not plan_id:
        return {"error": "plan_id is required for add_item"}
    if not title:
        return {"error": "title is required for add_item"}

    # Verify plan exists and is active
    cursor = await db.execute(
        "SELECT status FROM work_plans WHERE id = ?", (plan_id,)
    )
    row = await cursor.fetchone()
    if not row:
        return {"error": f"Plan not found: {plan_id}"}
    if row[0] in _TERMINAL_PLAN_STATUSES:
        return {"error": f"Plan is {row[0]} — cannot add items"}

    # Auto-increment ordinal if not provided
    ordinal = arguments.get("ordinal")
    if ordinal is None:
        cursor = await db.execute(
            "SELECT COALESCE(MAX(ordinal), -1) + 1 FROM work_items WHERE plan_id = ?",
            (plan_id,),
        )
        ordinal_row = await cursor.fetchone()
        ordinal = ordinal_row[0] if ordinal_row else 0

    item_id = str(ULID())
    now = datetime.now(timezone.utc).isoformat()

    await db.execute(
        "INSERT INTO work_items (id, plan_id, title, status, ordinal, notes, files_changed, created_at, updated_at) "
        "VALUES (?, ?, ?, 'new', ?, '[]', '[]', ?, ?)",
        (item_id, plan_id, title, ordinal, now, now),
    )
    await db.commit()

    logger.info("work_plan add_item id=%s plan=%s title=%s ordinal=%d", item_id, plan_id, title[:60], ordinal)

    result: dict[str, Any] = {
        "status": "added",
        "item_id": item_id,
        "plan_id": plan_id,
        "title": title,
        "ordinal": ordinal,
    }

    result["_sse_event"] = {
        "event": "item_created",
        "data": {"plan_id": plan_id, "item_id": item_id, "title": title, "ordinal": ordinal},
    }

    return result


async def _update_item(
    arguments: dict[str, Any],
    db: aiosqlite.Connection,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Update a work item's status, notes, context_snapshot, or files_changed."""
    item_id = arguments.get("item_id", "")
    if not item_id:
        return {"error": "item_id is required for update_item"}

    # Load current item
    cursor = await db.execute(
        "SELECT id, plan_id, status, notes, files_changed FROM work_items WHERE id = ?",
        (item_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return {"error": f"Item not found: {item_id}"}

    plan_id = row[1]
    current_status = row[2]
    current_notes_raw = row[3] or "[]"
    current_files_raw = row[4] or "[]"

    # Parse existing notes and files
    try:
        current_notes = json.loads(current_notes_raw) if isinstance(current_notes_raw, str) else current_notes_raw
    except json.JSONDecodeError:
        current_notes = []
    try:
        current_files = json.loads(current_files_raw) if isinstance(current_files_raw, str) else current_files_raw
    except json.JSONDecodeError:
        current_files = []

    updates: list[str] = []
    params: list[Any] = []
    now = datetime.now(timezone.utc).isoformat()

    # Status update
    new_status = arguments.get("status")
    if new_status:
        if new_status not in _VALID_ITEM_STATUSES:
            return {"error": f"Invalid status: {new_status}. Valid: {sorted(_VALID_ITEM_STATUSES)}"}
        updates.append("status = ?")
        params.append(new_status)

        if new_status == "in_progress" and current_status != "in_progress":
            updates.append("started_at = ?")
            params.append(now)
        if new_status in _TERMINAL_ITEM_STATUSES:
            updates.append("completed_at = ?")
            params.append(now)

    # Notes: append (never overwrite)
    note_text = arguments.get("notes")
    if note_text and isinstance(note_text, str):
        current_notes.append({"at": now, "text": note_text})
        updates.append("notes = ?")
        params.append(json.dumps(current_notes))

    # Context snapshot
    context_snapshot = arguments.get("context_snapshot")
    if context_snapshot is not None:
        snapshot_str = json.dumps(context_snapshot) if isinstance(context_snapshot, dict) else str(context_snapshot)
        updates.append("context_snapshot = ?")
        params.append(snapshot_str)

    # Files changed: merge (deduplicate)
    files_changed = arguments.get("files_changed")
    if files_changed and isinstance(files_changed, list):
        merged_files = list(dict.fromkeys(current_files + files_changed))
        updates.append("files_changed = ?")
        params.append(json.dumps(merged_files))

    if not updates:
        return {"error": "No updates provided. Provide at least one of: status, notes, context_snapshot, files_changed"}

    updates.append("updated_at = ?")
    params.append(now)
    params.append(item_id)

    sql = f"UPDATE work_items SET {', '.join(updates)} WHERE id = ?"
    await db.execute(sql, params)
    await db.commit()

    effective_status = new_status or current_status
    logger.info("work_plan update_item id=%s status=%s notes_count=%d", item_id, effective_status, len(current_notes))

    result: dict[str, Any] = {
        "status": "updated",
        "item_id": item_id,
        "plan_id": plan_id,
        "item_status": effective_status,
        "notes_count": len(current_notes),
    }

    sse_data: dict[str, Any] = {"plan_id": plan_id, "item_id": item_id, "status": effective_status}
    if note_text:
        sse_data["notes"] = note_text
    if files_changed:
        sse_data["files_changed"] = files_changed
    result["_sse_event"] = {"event": "item_updated", "data": sse_data}

    return result


async def _complete_plan(
    arguments: dict[str, Any],
    db: aiosqlite.Connection,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Set a plan to a terminal status."""
    plan_id = arguments.get("plan_id", "")
    status = arguments.get("status", "completed")

    if not plan_id:
        return {"error": "plan_id is required for complete_plan"}
    if status not in _TERMINAL_PLAN_STATUSES:
        return {"error": f"Invalid terminal status: {status}. Valid: {sorted(_TERMINAL_PLAN_STATUSES)}"}

    cursor = await db.execute(
        "SELECT status FROM work_plans WHERE id = ?", (plan_id,)
    )
    row = await cursor.fetchone()
    if not row:
        return {"error": f"Plan not found: {plan_id}"}
    if row[0] in _TERMINAL_PLAN_STATUSES:
        return {"error": f"Plan already in terminal status: {row[0]}"}

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE work_plans SET status = ?, completed_at = ?, updated_at = ? WHERE id = ?",
        (status, now, now, plan_id),
    )
    await db.commit()

    logger.info("work_plan complete_plan id=%s status=%s", plan_id, status)

    result: dict[str, Any] = {
        "status": "completed",
        "plan_id": plan_id,
        "plan_status": status,
    }

    result["_sse_event"] = {
        "event": "plan_completed",
        "data": {"plan_id": plan_id, "status": status},
    }

    return result


async def _get_plan(
    arguments: dict[str, Any],
    db: aiosqlite.Connection,
) -> dict[str, Any]:
    """Get a plan with all its items."""
    plan_id = arguments.get("plan_id", "")
    if not plan_id:
        return {"error": "plan_id is required for get_plan"}

    cursor = await db.execute(
        "SELECT id, agent_id, conversation_id, parent_plan_id, title, status, "
        "created_at, updated_at, completed_at FROM work_plans WHERE id = ?",
        (plan_id,),
    )
    plan_row = await cursor.fetchone()
    if not plan_row:
        return {"error": f"Plan not found: {plan_id}"}

    plan = {
        "id": plan_row[0],
        "agent_id": plan_row[1],
        "conversation_id": plan_row[2],
        "parent_plan_id": plan_row[3],
        "title": plan_row[4],
        "status": plan_row[5],
        "created_at": plan_row[6],
        "updated_at": plan_row[7],
        "completed_at": plan_row[8],
    }

    cursor = await db.execute(
        "SELECT id, title, status, ordinal, context_snapshot, notes, files_changed, "
        "started_at, completed_at, created_at, updated_at "
        "FROM work_items WHERE plan_id = ? ORDER BY ordinal",
        (plan_id,),
    )
    items = []
    for item_row in await cursor.fetchall():
        notes_raw = item_row[5] or "[]"
        files_raw = item_row[6] or "[]"
        try:
            notes = json.loads(notes_raw) if isinstance(notes_raw, str) else notes_raw
        except json.JSONDecodeError:
            notes = []
        try:
            files = json.loads(files_raw) if isinstance(files_raw, str) else files_raw
        except json.JSONDecodeError:
            files = []
        try:
            snapshot = json.loads(item_row[4]) if item_row[4] else None
        except (json.JSONDecodeError, TypeError):
            snapshot = None

        items.append({
            "id": item_row[0],
            "title": item_row[1],
            "status": item_row[2],
            "ordinal": item_row[3],
            "context_snapshot": snapshot,
            "notes": notes,
            "files_changed": files,
            "started_at": item_row[7],
            "completed_at": item_row[8],
            "created_at": item_row[9],
            "updated_at": item_row[10],
        })

    plan["items"] = items
    return plan


# ---------------------------------------------------------------------------
# Recovery helpers (used by worker.py)
# ---------------------------------------------------------------------------


async def load_active_plan(db: aiosqlite.Connection | None, agent_id: str) -> dict[str, Any] | None:
    """Load the most recent active work plan for an agent, with all items."""
    _own_db = False
    if db is None:
        db = await _get_plans_db()
        _own_db = True
    try:
        return await _load_active_plan_impl(db, agent_id)
    finally:
        if _own_db:
            await db.close()


async def _load_active_plan_impl(db: aiosqlite.Connection, agent_id: str) -> dict[str, Any] | None:
    cursor = await db.execute(
        "SELECT id FROM work_plans WHERE agent_id = ? AND status = 'active' "
        "ORDER BY updated_at DESC LIMIT 1",
        (agent_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return None

    plan_id = row[0]
    plan = await _get_plan({"plan_id": plan_id}, db)
    if "error" in plan:
        return None
    return plan


def format_recovery_context(plan: dict[str, Any]) -> str:
    """Build a human-readable recovery context message from a plan."""
    lines = [f"[Resuming work plan: \"{plan['title']}\"]", ""]

    items = plan.get("items", [])
    completed = [i for i in items if i["status"] in ("done", "complete", "tested", "approved")]
    in_progress = [i for i in items if i["status"] == "in_progress"]
    remaining = [i for i in items if i["status"] in ("new", "blocked")]

    if completed:
        lines.append("Completed:")
        for item in completed:
            lines.append(f"  - \u2705 {item['title']}")
            # Show last note if available
            if item.get("notes"):
                last_note = item["notes"][-1] if isinstance(item["notes"], list) else None
                if last_note and isinstance(last_note, dict):
                    lines.append(f"    Last note: {last_note.get('text', '')[:200]}")
        lines.append("")

    if in_progress:
        lines.append("In Progress:")
        for item in in_progress:
            lines.append(f"  - \U0001f504 {item['title']}")
            # Show context snapshot if available
            if item.get("context_snapshot"):
                snapshot = item["context_snapshot"]
                if isinstance(snapshot, dict):
                    if snapshot.get("decisions_made"):
                        lines.append(f"    Decisions: {', '.join(str(d) for d in snapshot['decisions_made'][:3])}")
                    if snapshot.get("remaining_work"):
                        lines.append(f"    Remaining: {', '.join(str(r) for r in snapshot['remaining_work'][:3])}")
                    if snapshot.get("files_read"):
                        files = list(snapshot["files_read"].keys())[:5]
                        lines.append(f"    Files read: {', '.join(files)}")
                elif isinstance(snapshot, str):
                    lines.append(f"    Context: {snapshot[:300]}")
            # Show recent notes
            if item.get("notes") and isinstance(item["notes"], list):
                recent = item["notes"][-3:]
                for note in recent:
                    if isinstance(note, dict):
                        lines.append(f"    Note: {note.get('text', '')[:200]}")
            # Show files changed
            if item.get("files_changed"):
                lines.append(f"    Files changed: {', '.join(item['files_changed'][:10])}")
        lines.append("")

    if remaining:
        lines.append("Remaining:")
        for item in remaining:
            prefix = "\u2b1c" if item["status"] == "new" else "\U0001f6d1"
            lines.append(f"  - {prefix} {item['title']}")
        lines.append("")

    return "\n".join(lines)


async def checkpoint_active_plan(
    db: aiosqlite.Connection | None,
    agent_id: str,
    context_note: str = "Max iterations reached — saving checkpoint",
) -> bool:
    """Save a checkpoint on the active plan's in-progress item.

    Called when max iterations is hit or on crash recovery.
    Returns True if a checkpoint was saved.
    """
    _own_db = False
    if db is None:
        db = await _get_plans_db()
        _own_db = True
    try:
        plan = await load_active_plan(db, agent_id)
        if not plan:
            return False

        items = plan.get("items", [])
        in_progress = [i for i in items if i["status"] == "in_progress"]
        if not in_progress:
            return False

        current_item = in_progress[0]
        now = datetime.now(timezone.utc).isoformat()

        # Append a note about the checkpoint
        notes = current_item.get("notes", [])
        if not isinstance(notes, list):
            notes = []
        notes.append({"at": now, "text": context_note})

        await db.execute(
            "UPDATE work_items SET notes = ?, updated_at = ? WHERE id = ?",
            (json.dumps(notes), now, current_item["id"]),
        )
        await db.commit()

        logger.info("Checkpoint saved for item %s in plan %s", current_item["id"], plan["id"])
        return True
    finally:
        if _own_db:
            await db.close()
