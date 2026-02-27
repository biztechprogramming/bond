"""Plans API — CRUD for work plans and items."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.session import get_db

logger = logging.getLogger("bond.api.plans")

router = APIRouter(prefix="/plans", tags=["plans"])


# -- Pydantic models --


class ItemStatusUpdate(BaseModel):
    status: str


# -- List plans --


@router.get("")
async def list_plans(
    agent_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List work plans. Active plans first, then by updated_at DESC."""
    conditions = []
    params: dict = {"limit": limit, "offset": offset}

    if agent_id:
        conditions.append("agent_id = :agent_id")
        params["agent_id"] = agent_id
    if status:
        conditions.append("status = :status")
        params["status"] = status

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    sql = text(f"""
        SELECT id, agent_id, conversation_id, parent_plan_id, title, status,
               created_at, updated_at, completed_at
        FROM work_plans
        {where}
        ORDER BY
            CASE WHEN status = 'active' THEN 0
                 WHEN status = 'paused' THEN 1
                 ELSE 2 END,
            updated_at DESC
        LIMIT :limit OFFSET :offset
    """)

    result = await db.execute(sql, params)
    rows = result.fetchall()

    plans = []
    for row in rows:
        plans.append({
            "id": row[0],
            "agent_id": row[1],
            "conversation_id": row[2],
            "parent_plan_id": row[3],
            "title": row[4],
            "status": row[5],
            "created_at": row[6],
            "updated_at": row[7],
            "completed_at": row[8],
        })

    return plans


# -- Get plan with items --


@router.get("/{plan_id}")
async def get_plan(plan_id: str, db: AsyncSession = Depends(get_db)):
    """Get a plan with all its items, ordered by ordinal."""
    result = await db.execute(
        text("""
            SELECT id, agent_id, conversation_id, parent_plan_id, title, status,
                   created_at, updated_at, completed_at
            FROM work_plans WHERE id = :plan_id
        """),
        {"plan_id": plan_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Plan not found")

    plan = {
        "id": row[0],
        "agent_id": row[1],
        "conversation_id": row[2],
        "parent_plan_id": row[3],
        "title": row[4],
        "status": row[5],
        "created_at": row[6],
        "updated_at": row[7],
        "completed_at": row[8],
    }

    items_result = await db.execute(
        text("""
            SELECT id, title, status, ordinal, context_snapshot, notes,
                   files_changed, started_at, completed_at, created_at, updated_at
            FROM work_items WHERE plan_id = :plan_id
            ORDER BY ordinal
        """),
        {"plan_id": plan_id},
    )
    items = []
    for item_row in items_result.fetchall():
        notes_raw = item_row[5] or "[]"
        files_raw = item_row[6] or "[]"
        try:
            notes = json.loads(notes_raw) if isinstance(notes_raw, str) else notes_raw
        except (json.JSONDecodeError, TypeError):
            notes = []
        try:
            files = json.loads(files_raw) if isinstance(files_raw, str) else files_raw
        except (json.JSONDecodeError, TypeError):
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


# -- Update item status --


@router.patch("/{plan_id}/items/{item_id}")
async def update_item_status(
    plan_id: str,
    item_id: str,
    body: ItemStatusUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a work item's status (user-driven changes from UI)."""
    valid_statuses = {
        "new", "in_progress", "done", "in_review", "approved",
        "in_test", "tested", "complete", "blocked", "failed",
    }
    if body.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status: {body.status}")

    # Verify item exists and belongs to plan
    result = await db.execute(
        text("SELECT id, status FROM work_items WHERE id = :item_id AND plan_id = :plan_id"),
        {"item_id": item_id, "plan_id": plan_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Item not found in this plan")

    now = datetime.now(timezone.utc).isoformat()
    terminal_statuses = {"done", "complete", "failed"}
    updates = ["status = :status", "updated_at = :now"]
    params: dict = {"status": body.status, "now": now, "item_id": item_id}

    if body.status == "in_progress" and row[1] != "in_progress":
        updates.append("started_at = :now")
    if body.status in terminal_statuses:
        updates.append("completed_at = :now")

    await db.execute(
        text(f"UPDATE work_items SET {', '.join(updates)} WHERE id = :item_id"),
        params,
    )
    await db.commit()

    return {"status": "updated", "item_id": item_id, "new_status": body.status}


# -- Cancel plan --


@router.delete("/{plan_id}")
async def cancel_plan(plan_id: str, db: AsyncSession = Depends(get_db)):
    """Cancel a plan (set status to cancelled)."""
    result = await db.execute(
        text("SELECT status FROM work_plans WHERE id = :plan_id"),
        {"plan_id": plan_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Plan not found")
    if row[0] in ("completed", "failed", "cancelled"):
        raise HTTPException(status_code=400, detail=f"Plan already in terminal status: {row[0]}")

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        text("UPDATE work_plans SET status = 'cancelled', completed_at = :now, updated_at = :now WHERE id = :plan_id"),
        {"plan_id": plan_id, "now": now},
    )
    await db.commit()

    return {"status": "cancelled", "plan_id": plan_id}


# -- Resume plan --


@router.post("/{plan_id}/resume")
async def resume_plan(plan_id: str, db: AsyncSession = Depends(get_db)):
    """Return recovery context for a plan. If completed/failed, indicates a child plan should be created."""
    result = await db.execute(
        text("""
            SELECT id, agent_id, title, status
            FROM work_plans WHERE id = :plan_id
        """),
        {"plan_id": plan_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Plan not found")

    plan_status = row[3]

    # Get items
    items_result = await db.execute(
        text("""
            SELECT id, title, status, ordinal, context_snapshot, notes, files_changed
            FROM work_items WHERE plan_id = :plan_id ORDER BY ordinal
        """),
        {"plan_id": plan_id},
    )
    items = []
    for item_row in items_result.fetchall():
        try:
            snapshot = json.loads(item_row[4]) if item_row[4] else None
        except (json.JSONDecodeError, TypeError):
            snapshot = None
        try:
            notes = json.loads(item_row[5]) if isinstance(item_row[5], str) else item_row[5] or []
        except (json.JSONDecodeError, TypeError):
            notes = []
        try:
            files = json.loads(item_row[6]) if isinstance(item_row[6], str) else item_row[6] or []
        except (json.JSONDecodeError, TypeError):
            files = []

        items.append({
            "id": item_row[0],
            "title": item_row[1],
            "status": item_row[2],
            "ordinal": item_row[3],
            "context_snapshot": snapshot,
            "notes": notes,
            "files_changed": files,
        })

    # Build recovery context
    plan = {"id": row[0], "agent_id": row[1], "title": row[2], "status": plan_status, "items": items}

    from backend.app.agent.tools.work_plan import format_recovery_context
    recovery_context = format_recovery_context(plan)

    response = {
        "plan_id": plan_id,
        "plan_status": plan_status,
        "recovery_context": recovery_context,
        "should_create_child": plan_status in ("completed", "failed", "cancelled"),
    }

    return response


# -- Plan lineage --


@router.get("/{plan_id}/lineage")
async def plan_lineage(plan_id: str, db: AsyncSession = Depends(get_db)):
    """Return parent chain + children for a plan."""
    # Get the plan itself
    result = await db.execute(
        text("SELECT id, parent_plan_id, title, status FROM work_plans WHERE id = :plan_id"),
        {"plan_id": plan_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Plan not found")

    current = {"id": row[0], "parent_plan_id": row[1], "title": row[2], "status": row[3]}

    # Walk up the parent chain
    parents = []
    parent_id = current["parent_plan_id"]
    visited = {plan_id}
    while parent_id and parent_id not in visited:
        visited.add(parent_id)
        result = await db.execute(
            text("SELECT id, parent_plan_id, title, status FROM work_plans WHERE id = :pid"),
            {"pid": parent_id},
        )
        prow = result.fetchone()
        if not prow:
            break
        parents.append({"id": prow[0], "parent_plan_id": prow[1], "title": prow[2], "status": prow[3]})
        parent_id = prow[1]

    parents.reverse()  # oldest first

    # Get children
    result = await db.execute(
        text("SELECT id, title, status FROM work_plans WHERE parent_plan_id = :plan_id ORDER BY created_at"),
        {"plan_id": plan_id},
    )
    children = [{"id": r[0], "title": r[1], "status": r[2]} for r in result.fetchall()]

    return {
        "parents": parents,
        "current": current,
        "children": children,
    }
