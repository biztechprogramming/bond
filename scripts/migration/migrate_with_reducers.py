#!/usr/bin/env python3
"""Migrate work_plans and work_items using import reducers."""

import asyncio
import sqlite3
import time
from datetime import datetime
from backend.app.core.spacetimedb import StdbClient

def escape_sql(value):
    """Escape single quotes for SQL."""
    if value is None:
        return ''
    return str(value).replace("'", "''")

async def migrate_with_reducers():
    # Connect to SQLite
    sqlite_conn = sqlite3.connect('knowledge.db')
    sqlite_conn.row_factory = sqlite3.Row
    
    # Connect to SpacetimeDB
    stdb = StdbClient()
    
    print("=== Migrating work_plans and work_items using import reducers ===\n")
    
    # Migrate work_plans using import_work_plan reducer
    print("Migrating work_plans...")
    cursor = sqlite_conn.cursor()
    cursor.execute("""
        SELECT id, agent_id, conversation_id, parent_plan_id, title, status,
               created_at, updated_at, completed_at
        FROM work_plans
    """)
    rows = cursor.fetchall()
    
    migrated = 0
    for row in rows:
        plan_id = row["id"]
        agent_id = row["agent_id"]
        conversation_id = escape_sql(row["conversation_id"])
        parent_plan_id = escape_sql(row["parent_plan_id"])
        title = escape_sql(row["title"])
        status = escape_sql(row["status"]) or "active"
        created_at = int(datetime.fromisoformat(row["created_at"]).timestamp() * 1000) if row["created_at"] else int(time.time() * 1000)
        updated_at = int(datetime.fromisoformat(row["updated_at"]).timestamp() * 1000) if row["updated_at"] else created_at
        completed_at = int(datetime.fromisoformat(row["completed_at"]).timestamp() * 1000) if row["completed_at"] else 0
        
        # Build arguments for import_work_plan reducer
        # Note: The reducer expects completedAt to be optional
        # If completed_at is 0, we need to pass None or omit it?
        # Let's try passing 0 and see if it works
        args = [
            plan_id,
            agent_id,
            conversation_id,
            parent_plan_id,
            title,
            status,
            created_at,
            updated_at,
            completed_at if completed_at > 0 else 0  # Pass 0 for "not set"
        ]
        
        # Try calling the reducer
        success = await stdb.call_reducer("import_work_plan", args)
        if success:
            migrated += 1
            if migrated % 20 == 0:
                print(f"  Migrated {migrated} work plans...")
        else:
            print(f"  Failed to migrate work plan {plan_id}")
    
    print(f"  Migrated {migrated} work plans total")
    
    # Migrate work_items using import_work_item reducer
    print("\nMigrating work_items...")
    cursor.execute("""
        SELECT id, plan_id, title, status, ordinal, context_snapshot,
               notes, files_changed, started_at, completed_at,
               created_at, updated_at, description
        FROM work_items
    """)
    rows = cursor.fetchall()
    
    migrated = 0
    for row in rows:
        item_id = row["id"]
        plan_id = row["plan_id"]
        title = escape_sql(row["title"])
        status = escape_sql(row["status"]) or "new"
        ordinal = row["ordinal"] or 0
        context_snapshot = escape_sql(row["context_snapshot"]) or "{}"
        notes = escape_sql(row["notes"]) or "[]"
        files_changed = escape_sql(row["files_changed"]) or "[]"
        started_at = int(datetime.fromisoformat(row["started_at"]).timestamp() * 1000) if row["started_at"] else 0
        completed_at = int(datetime.fromisoformat(row["completed_at"]).timestamp() * 1000) if row["completed_at"] else 0
        created_at = int(datetime.fromisoformat(row["created_at"]).timestamp() * 1000) if row["created_at"] else int(time.time() * 1000)
        updated_at = int(datetime.fromisoformat(row["updated_at"]).timestamp() * 1000) if row["updated_at"] else created_at
        description = escape_sql(row.get("description", "")) or ""
        
        # Build arguments for import_work_item reducer
        args = [
            item_id,
            plan_id,
            title,
            status,
            ordinal,
            context_snapshot,
            notes,
            files_changed,
            started_at if started_at > 0 else 0,
            completed_at if completed_at > 0 else 0,
            created_at,
            updated_at,
            description
        ]
        
        # Try calling the reducer
        success = await stdb.call_reducer("import_work_item", args)
        if success:
            migrated += 1
            if migrated % 20 == 0:
                print(f"  Migrated {migrated} work items...")
        else:
            print(f"  Failed to migrate work item {item_id}")
    
    print(f"  Migrated {migrated} work items total")
    
    print("\n=== Migration complete ===")
    
    # Verify migration
    print("\nVerifying migration...")
    try:
        plans_count = await stdb.query("SELECT COUNT(*) as count FROM work_plans")
        items_count = await stdb.query("SELECT COUNT(*) as count FROM work_items")
        
        print(f"  work_plans: {plans_count[0]['count'] if plans_count else 0} rows")
        print(f"  work_items: {items_count[0]['count'] if items_count else 0} rows")
    except Exception as e:
        print(f"  Error verifying: {e}")
    
    await stdb.close()
    sqlite_conn.close()

if __name__ == "__main__":
    asyncio.run(migrate_with_reducers())