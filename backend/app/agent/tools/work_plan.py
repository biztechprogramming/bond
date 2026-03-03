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

from ulid import ULID

logger = logging.getLogger("bond.agent.tools.work_plan")

# Gateway API URL — set by the sandbox manager when running in a container.
# On the host, set BOND_API_URL to the Gateway base URL (e.g. http://localhost:18792).
# All plan data lives in SpacetimeDB; there is no SQLite fallback.
_BOND_API_URL = os.environ.get("BOND_API_URL", "")


def _use_api() -> bool:
    """Return True when the Gateway API URL is configured."""
    return bool(_BOND_API_URL)

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

    if not _use_api():
        logger.error("work_plan: BOND_API_URL is not set — SpacetimeDB is required, no SQLite fallback")
        return {"error": "BOND_API_URL is not configured. All plan data lives in SpacetimeDB; set BOND_API_URL to the Gateway URL."}

    return await _handle_via_api(action, arguments, agent_id, context)


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
                item_id = arguments.get("item_id", "")
                if not item_id:
                    return {"error": "item_id is required for update_item"}
                body = {}
                if "status" in arguments:
                    body["status"] = arguments["status"]
                if "notes" in arguments:
                    body["notes"] = arguments["notes"]
                if "context_snapshot" in arguments:
                    body["context_snapshot"] = arguments["context_snapshot"]
                if "files_changed" in arguments:
                    body["files_changed"] = arguments["files_changed"]
                plan_id = arguments.get("plan_id", "")
                # Use flat /items/:id endpoint — plan_id not needed by the reducer.
                # Fall back to nested URL if plan_id is known (both routes work).
                item_url = (
                    f"{url}/{plan_id}/items/{item_id}" if plan_id
                    else f"{base}/api/v1/items/{item_id}"
                )
                resp = await client.put(item_url, json=body)
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


# ---------------------------------------------------------------------------
# NOTE: The SQLite-backed _create_plan / _add_item / _update_item /
# _complete_plan / _get_plan functions have been removed. All plan data
# lives in SpacetimeDB. Use the Gateway API at BOND_API_URL/api/v1/plans.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Recovery helpers (used by worker.py)
# ---------------------------------------------------------------------------


async def load_active_plan(db: None, agent_id: str) -> dict[str, Any] | None:
    """Load the most recent active work plan for an agent via the Gateway API.

    The ``db`` argument is accepted for backwards-compatibility but is ignored;
    all data comes from SpacetimeDB.
    """
    if not _use_api():
        logger.warning("load_active_plan: BOND_API_URL not set, cannot load plan from SpacetimeDB")
        return None
    try:
        import httpx
        base = _BOND_API_URL.rstrip("/")
        url = f"{base}/api/v1/plans?agent_id={agent_id}&status=active&limit=1"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            plans = resp.json()
        if not plans:
            return None
        # Fetch full plan with items
        plan_id = plans[0]["id"]
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{base}/api/v1/plans/{plan_id}")
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning("load_active_plan API call failed: %s", e)
        return None


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
    db: None,
    agent_id: str,
    context_note: str = "Max iterations reached — saving checkpoint",
) -> bool:
    """Save a checkpoint on the active plan's in-progress item via the Gateway API.

    The ``db`` argument is accepted for backwards-compatibility but is ignored.
    Returns True if a checkpoint was saved.
    """
    if not _use_api():
        logger.warning("checkpoint_active_plan: BOND_API_URL not set, cannot checkpoint via SpacetimeDB")
        return False
    try:
        import httpx
        plan = await load_active_plan(None, agent_id)
        if not plan:
            return False

        items = plan.get("items", [])
        in_progress = [i for i in items if i["status"] == "in_progress"]
        if not in_progress:
            return False

        current_item = in_progress[0]
        now = datetime.now(timezone.utc).isoformat()

        notes = current_item.get("notes", [])
        if not isinstance(notes, list):
            notes = []
        notes.append({"at": now, "text": context_note})

        base = _BOND_API_URL.rstrip("/")
        plan_id = plan["id"]
        item_id = current_item["id"]
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.put(
                f"{base}/api/v1/plans/{plan_id}/items/{item_id}",
                json={"notes": notes},
            )
            resp.raise_for_status()

        logger.info("Checkpoint saved for item %s in plan %s", item_id, plan_id)
        return True
    except Exception as e:
        logger.warning("checkpoint_active_plan API call failed: %s", e)
        return False
