"""Plans API — backed by SpacetimeDB via the Gateway. No SQLite."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from ulid import ULID

from backend.app.config import get_settings

logger = logging.getLogger("bond.api.plans")

router = APIRouter(prefix="/plans", tags=["plans"])

# Separate router for flat /items/:id routes (no plan_id in path)
items_router = APIRouter(prefix="/items", tags=["items"])


def _gateway_url() -> str:
    settings = get_settings()
    return f"http://localhost:{settings.gateway_port}/api/v1"


# ── Pydantic models ──


class CreatePlanRequest(BaseModel):
    title: str
    agent_id: str
    conversation_id: str | None = None
    parent_plan_id: str | None = None


class AddItemRequest(BaseModel):
    title: str
    ordinal: int | None = None
    description: str | None = None


class UpdateItemRequest(BaseModel):
    title: str | None = None
    status: str | None = None
    notes: str | None = None
    context_snapshot: dict | None = None
    files_changed: list[str] | None = None
    description: str | None = None


class ItemStatusUpdate(BaseModel):
    status: str


class CompletePlanRequest(BaseModel):
    status: str = "completed"


# ── Helpers ──


async def _get(path: str) -> dict | list:
    base = _gateway_url()
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{base}{path}")
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail=resp.json().get("error", "Not found"))
        resp.raise_for_status()
        return resp.json()


async def _post(path: str, body: dict) -> dict:
    base = _gateway_url()
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{base}{path}", json=body)
        if resp.status_code >= 400:
            detail = resp.json().get("error", resp.text) if resp.headers.get("content-type", "").startswith("application/json") else resp.text
            raise HTTPException(status_code=resp.status_code, detail=detail)
        return resp.json()


async def _put(path: str, body: dict) -> dict:
    base = _gateway_url()
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.put(f"{base}{path}", json=body)
        if resp.status_code >= 400:
            detail = resp.json().get("error", resp.text) if resp.headers.get("content-type", "").startswith("application/json") else resp.text
            raise HTTPException(status_code=resp.status_code, detail=detail)
        return resp.json()


# ── Endpoints ──


@router.post("")
async def create_plan(body: CreatePlanRequest):
    result = await _post("/plans", {
        "title": body.title,
        "agent_id": body.agent_id,
        "conversation_id": body.conversation_id or "",
    })
    return {"status": "created", "plan_id": result["plan_id"], "title": body.title}


@router.post("/{plan_id}/items")
async def add_item(plan_id: str, body: AddItemRequest):
    payload: dict = {"title": body.title}
    if body.ordinal is not None:
        payload["ordinal"] = body.ordinal
    if body.description is not None:
        payload["description"] = body.description
    result = await _post(f"/plans/{plan_id}/items", payload)
    return {"status": "added", "item_id": result["item_id"], "plan_id": plan_id,
            "title": body.title, "ordinal": result.get("ordinal")}


@router.put("/{plan_id}/items/{item_id}")
async def update_item_full(plan_id: str, item_id: str, body: UpdateItemRequest):
    payload: dict = {}
    if body.title is not None:
        payload["title"] = body.title
    if body.status is not None:
        payload["status"] = body.status
    if body.notes is not None:
        payload["notes"] = body.notes
    if body.files_changed is not None:
        payload["files_changed"] = body.files_changed
    if body.description is not None:
        payload["description"] = body.description
    if not payload:
        raise HTTPException(status_code=400, detail="Provide at least one of: title, status, notes, files_changed, description")
    await _put(f"/plans/{plan_id}/items/{item_id}", payload)
    return {"status": "updated", "item_id": item_id}


@router.patch("/{plan_id}/items/{item_id}")
async def update_item_status(plan_id: str, item_id: str, body: ItemStatusUpdate):
    await _put(f"/items/{item_id}", {"status": body.status})
    return {"status": "updated", "item_id": item_id, "new_status": body.status}


@router.put("/items/{item_id}")
@items_router.put("/{item_id}")
async def update_item_flat(item_id: str, body: UpdateItemRequest):
    """Flat route — update an item without needing plan_id in the path."""
    payload: dict = {}
    if body.title is not None:
        payload["title"] = body.title
    if body.status is not None:
        payload["status"] = body.status
    if body.notes is not None:
        payload["notes"] = body.notes
    if body.files_changed is not None:
        payload["files_changed"] = body.files_changed
    if body.description is not None:
        payload["description"] = body.description
    if not payload:
        raise HTTPException(status_code=400, detail="Provide at least one of: title, status, notes, files_changed, description")
    await _put(f"/items/{item_id}", payload)
    return {"status": "updated", "item_id": item_id}


@router.post("/{plan_id}/complete")
async def complete_plan(plan_id: str, body: CompletePlanRequest):
    result = await _post(f"/plans/{plan_id}/complete", {"status": body.status})
    return result


@router.get("")
async def list_plans(
    agent_id: str | None = None,
    status: str | None = None,
    conversation_id: str | None = None,
    limit: int = Query(default=20, le=100),
):
    params: list[str] = []
    if agent_id:
        params.append(f"agent_id={agent_id}")
    if status:
        params.append(f"status={status}")
    if conversation_id:
        params.append(f"conversation_id={conversation_id}")
    params.append(f"limit={limit}")
    return await _get(f"/plans?{'&'.join(params)}")


@router.get("/{plan_id}")
async def get_plan(plan_id: str):
    return await _get(f"/plans/{plan_id}")


@router.delete("/{plan_id}")
async def delete_plan(plan_id: str):
    """Permanently delete a plan and all its items."""
    base = _gateway_url()
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.delete(f"{base}/plans/{plan_id}")
        if resp.status_code >= 400:
            detail = resp.json().get("error", resp.text) if resp.headers.get("content-type", "").startswith("application/json") else resp.text
            raise HTTPException(status_code=resp.status_code, detail=detail)
        return resp.json()


@router.post("/{plan_id}/cancel")
async def cancel_plan(plan_id: str):
    """Mark a plan as cancelled without deleting it."""
    result = await _post(f"/plans/{plan_id}/complete", {"status": "cancelled"})
    return result


@router.post("/{plan_id}/resume")
async def resume_plan(plan_id: str):
    plan = await _get(f"/plans/{plan_id}")
    from backend.app.agent.tools.work_plan import format_recovery_context
    return {
        "plan_id": plan_id,
        "plan_status": plan["status"],
        "recovery_context": format_recovery_context(plan),
        "should_create_child": plan["status"] in ("completed", "failed", "cancelled"),
    }


@router.get("/{plan_id}/lineage")
async def plan_lineage(plan_id: str):
    # Lineage is tracked via parent_plan_id on the plan object.
    # Walk the chain using get_plan calls.
    plan = await _get(f"/plans/{plan_id}")
    current = {"id": plan["id"], "title": plan["title"], "status": plan["status"]}

    parents = []
    parent_id = plan.get("parent_plan_id")
    visited = {plan_id}
    while parent_id and parent_id not in visited:
        visited.add(parent_id)
        try:
            p = await _get(f"/plans/{parent_id}")
            parents.append({"id": p["id"], "title": p["title"], "status": p["status"]})
            parent_id = p.get("parent_plan_id")
        except HTTPException:
            break
    parents.reverse()

    # Children: list all plans with matching parent_plan_id
    # (SpacetimeDB doesn't support this query directly yet — return empty for now)
    return {"parents": parents, "current": current, "children": []}
