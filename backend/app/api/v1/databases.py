"""Database connections API (Design Doc 107 — Faucet Integration)."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.app.core.spacetimedb import get_stdb
from backend.app.core.vault import Vault
from backend.app.models.database import (
    AccessTier,
    AgentDatabaseAssign,
    AgentDatabaseResponse,
    DatabaseConnectionCreate,
    DatabaseConnectionResponse,
    DatabaseConnectionUpdate,
    DatabaseDriver,
)
from backend.app.services.faucet_manager import faucet_manager

logger = logging.getLogger("bond.api.databases")

router = APIRouter(tags=["databases"])


def _escape_sql(value) -> str:
    if value is None:
        return ""
    return str(value).replace("'", "''")


def _gen_id() -> str:
    return uuid4().hex[:16]


def _ts_to_datetime(ts) -> datetime:
    try:
        return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return datetime.now(tz=timezone.utc)


# ── Database Connection CRUD ──────────────────────────────────────


@router.get("/databases")
async def list_databases():
    """List all database connections."""
    stdb = get_stdb()
    rows = await stdb.query("SELECT * FROM database_connections")

    # Count agents per database
    access_rows = await stdb.query("SELECT database_id FROM agent_database_access")
    agent_counts: dict[str, int] = {}
    for ar in access_rows:
        db_id = ar["database_id"]
        agent_counts[db_id] = agent_counts.get(db_id, 0) + 1

    results = []
    for row in rows:
        results.append({
            "id": row["id"],
            "name": row["name"],
            "driver": row["driver"],
            "description": row["description"],
            "status": row["status"],
            "agent_count": agent_counts.get(row["id"], 0),
            "created_at": _ts_to_datetime(row["created_at"]),
            "updated_at": _ts_to_datetime(row["updated_at"]),
        })
    return results


@router.post("/databases")
async def create_database(body: DatabaseConnectionCreate):
    """Create a new database connection."""
    stdb = get_stdb()
    vault = Vault()

    # Check name uniqueness
    existing = await stdb.query(f"SELECT id FROM database_connections WHERE name = '{_escape_sql(body.name)}'")
    if existing:
        raise HTTPException(status_code=400, detail=f"Database connection '{body.name}' already exists")

    db_id = _gen_id()
    vault_key = f"db_dsn_{db_id}"

    # Store DSN in vault
    vault.set(vault_key, body.dsn.get_secret_value())

    # Register with Faucet (best-effort)
    status = "active"
    try:
        await faucet_manager.add_database(body.name, body.driver.value, body.dsn.get_secret_value())
    except Exception as e:
        logger.warning("Failed to register database with Faucet: %s", e)
        status = "error"

    now = int(time.time() * 1000)
    await stdb.call_reducer("add_database_connection", [
        db_id,
        body.name,
        body.driver.value,
        body.description or "",
        status,
        vault_key,
    ])

    return {
        "id": db_id,
        "name": body.name,
        "driver": body.driver.value,
        "description": body.description,
        "status": status,
        "agent_count": 0,
        "created_at": _ts_to_datetime(now),
        "updated_at": _ts_to_datetime(now),
    }


@router.get("/databases/{db_id}")
async def get_database(db_id: str):
    """Get a single database connection."""
    stdb = get_stdb()
    rows = await stdb.query(f"SELECT * FROM database_connections WHERE id = '{_escape_sql(db_id)}'")
    if not rows:
        raise HTTPException(status_code=404, detail="Database connection not found")

    row = rows[0]
    access_rows = await stdb.query(f"SELECT id FROM agent_database_access WHERE database_id = '{_escape_sql(db_id)}'")

    return {
        "id": row["id"],
        "name": row["name"],
        "driver": row["driver"],
        "description": row["description"],
        "status": row["status"],
        "agent_count": len(access_rows),
        "created_at": _ts_to_datetime(row["created_at"]),
        "updated_at": _ts_to_datetime(row["updated_at"]),
    }


@router.put("/databases/{db_id}")
async def update_database(db_id: str, body: DatabaseConnectionUpdate):
    """Update a database connection."""
    stdb = get_stdb()
    rows = await stdb.query(f"SELECT * FROM database_connections WHERE id = '{_escape_sql(db_id)}'")
    if not rows:
        raise HTTPException(status_code=404, detail="Database connection not found")

    row = rows[0]
    vault = Vault()

    # Update DSN in vault if provided
    if body.dsn:
        vault_key = row["dsn_vault_ref"]
        vault.set(vault_key, body.dsn.get_secret_value())

    description = body.description if body.description is not None else row["description"]

    await stdb.call_reducer("update_database_connection", [
        db_id,
        row["name"],
        row["driver"],
        description,
        row["status"],
        row["dsn_vault_ref"],
    ])

    access_rows = await stdb.query(f"SELECT id FROM agent_database_access WHERE database_id = '{_escape_sql(db_id)}'")
    return {
        "id": db_id,
        "name": row["name"],
        "driver": row["driver"],
        "description": description,
        "status": row["status"],
        "agent_count": len(access_rows),
        "created_at": _ts_to_datetime(row["created_at"]),
        "updated_at": _ts_to_datetime(int(time.time() * 1000)),
    }


@router.delete("/databases/{db_id}")
async def delete_database(db_id: str):
    """Delete a database connection."""
    stdb = get_stdb()
    rows = await stdb.query(f"SELECT * FROM database_connections WHERE id = '{_escape_sql(db_id)}'")
    if not rows:
        raise HTTPException(status_code=404, detail="Database connection not found")

    row = rows[0]
    vault = Vault()

    # Remove from Faucet (best-effort)
    try:
        await faucet_manager.remove_database(row["name"])
    except Exception as e:
        logger.warning("Failed to remove database from Faucet: %s", e)

    # Delete vault entry
    vault.delete(row["dsn_vault_ref"])

    # Delete access rows vault entries
    access_rows = await stdb.query(f"SELECT faucet_api_key_vault_ref FROM agent_database_access WHERE database_id = '{_escape_sql(db_id)}'")
    for ar in access_rows:
        vault_ref = ar.get("faucet_api_key_vault_ref")
        if vault_ref:
            vault.delete(vault_ref)

    await stdb.call_reducer("delete_database_connection", [db_id])
    return {"success": True, "message": f"Database connection {db_id} deleted"}


@router.post("/databases/{db_id}/test")
async def test_database(db_id: str):
    """Test a database connection via Faucet."""
    stdb = get_stdb()
    rows = await stdb.query(f"SELECT * FROM database_connections WHERE id = '{_escape_sql(db_id)}'")
    if not rows:
        raise HTTPException(status_code=404, detail="Database connection not found")

    row = rows[0]
    vault = Vault()
    dsn = vault.get(row["dsn_vault_ref"])
    if not dsn:
        raise HTTPException(status_code=400, detail="DSN not found in vault")

    try:
        await faucet_manager.add_database(row["name"], row["driver"], dsn)
        health = await faucet_manager.health_check()
        return {"success": True, "status": "connected", "health": health}
    except Exception as e:
        return {"success": False, "status": "error", "error": str(e)}


# ── Agent Database Access ─────────────────────────────────────────


@router.get("/agents/{agent_id}/databases")
async def list_agent_databases(agent_id: str):
    """List databases assigned to an agent."""
    stdb = get_stdb()
    access_rows = await stdb.query(
        f"SELECT * FROM agent_database_access WHERE agent_id = '{_escape_sql(agent_id)}'"
    )

    results = []
    for ar in access_rows:
        db_rows = await stdb.query(
            f"SELECT name, driver FROM database_connections WHERE id = '{_escape_sql(ar['database_id'])}'"
        )
        db_name = db_rows[0]["name"] if db_rows else "unknown"
        db_driver = db_rows[0]["driver"] if db_rows else "unknown"

        results.append({
            "id": ar["id"],
            "database_id": ar["database_id"],
            "database_name": db_name,
            "driver": db_driver,
            "access_tier": ar["access_tier"],
            "status": ar["status"],
            "assigned_at": _ts_to_datetime(ar["assigned_at"]),
        })
    return results


@router.post("/agents/{agent_id}/databases")
async def assign_agent_database(agent_id: str, body: AgentDatabaseAssign):
    """Assign a database to an agent. Supports inline creation."""
    stdb = get_stdb()
    vault = Vault()

    # Verify agent exists
    agent_rows = await stdb.query(f"SELECT id FROM agents WHERE id = '{_escape_sql(agent_id)}'")
    if not agent_rows:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Handle inline database creation
    if body.connection:
        db_resp = await create_database(body.connection)
        database_id = db_resp["id"]
        db_name = body.connection.name
    else:
        database_id = body.database_id
        db_rows = await stdb.query(
            f"SELECT name FROM database_connections WHERE id = '{_escape_sql(database_id)}'"
        )
        if not db_rows:
            raise HTTPException(status_code=404, detail="Database connection not found")
        db_name = db_rows[0]["name"]

    # Create Faucet role and API key (best-effort)
    role_name = f"{db_name}_{body.access_tier.value}"
    key_name = f"agent-{agent_id}-{db_name}"
    api_key_vault_ref = f"faucet_key_{_gen_id()}"

    try:
        permissions = ["read"] if body.access_tier == AccessTier.READ_ONLY else ["read", "write", "admin"]
        await faucet_manager.create_role(role_name, permissions)
        key_result = await faucet_manager.create_api_key(role_name, key_name)
        vault.set(api_key_vault_ref, key_result.get("key", ""))
    except Exception as e:
        logger.warning("Failed to create Faucet role/key: %s", e)

    access_id = _gen_id()
    await stdb.call_reducer("add_agent_database_access", [
        access_id,
        agent_id,
        database_id,
        body.access_tier.value,
        api_key_vault_ref,
        role_name,
        "active",
    ])

    # Fetch database details for response
    db_rows = await stdb.query(
        f"SELECT name, driver FROM database_connections WHERE id = '{_escape_sql(database_id)}'"
    )

    return {
        "id": access_id,
        "database_id": database_id,
        "database_name": db_rows[0]["name"] if db_rows else db_name,
        "driver": db_rows[0]["driver"] if db_rows else "unknown",
        "access_tier": body.access_tier.value,
        "status": "active",
        "assigned_at": _ts_to_datetime(int(time.time() * 1000)),
    }


@router.put("/agents/{agent_id}/databases/{db_id}")
async def update_agent_database(agent_id: str, db_id: str, body: BaseModel):
    """Update access tier for an agent-database assignment."""
    stdb = get_stdb()

    # Find existing access row
    access_rows = await stdb.query(
        f"SELECT * FROM agent_database_access WHERE agent_id = '{_escape_sql(agent_id)}' AND database_id = '{_escape_sql(db_id)}'"
    )
    if not access_rows:
        raise HTTPException(status_code=404, detail="Agent database access not found")

    ar = access_rows[0]
    # For now, just update the record — Faucet role changes would go here
    await stdb.call_reducer("update_agent_database_access", [
        ar["id"],
        ar["access_tier"],
        ar["faucet_api_key_vault_ref"],
        ar["faucet_role_name"],
        ar["status"],
    ])

    return {"success": True}


@router.delete("/agents/{agent_id}/databases/{db_id}")
async def remove_agent_database(agent_id: str, db_id: str):
    """Remove a database assignment from an agent."""
    stdb = get_stdb()
    vault = Vault()

    access_rows = await stdb.query(
        f"SELECT * FROM agent_database_access WHERE agent_id = '{_escape_sql(agent_id)}' AND database_id = '{_escape_sql(db_id)}'"
    )
    if not access_rows:
        raise HTTPException(status_code=404, detail="Agent database access not found")

    ar = access_rows[0]

    # Delete Faucet API key (best-effort)
    try:
        db_rows = await stdb.query(
            f"SELECT name FROM database_connections WHERE id = '{_escape_sql(db_id)}'"
        )
        if db_rows:
            key_name = f"agent-{agent_id}-{db_rows[0]['name']}"
            await faucet_manager.delete_api_key(key_name)
    except Exception as e:
        logger.warning("Failed to delete Faucet API key: %s", e)

    # Delete vault entry
    vault_ref = ar.get("faucet_api_key_vault_ref")
    if vault_ref:
        vault.delete(vault_ref)

    await stdb.call_reducer("delete_agent_database_access", [ar["id"]])
    return {"success": True, "message": "Database access removed"}
