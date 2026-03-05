#!/usr/bin/env python3
"""Migrate work_plans and work_items using import reducers with correct sum type syntax."""

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

def make_optional_u64(value):
    """Convert a U64 value to SpacetimeDB optional sum type.
    
    Returns:
        - {"some": value} if value > 0
        - {"none": []} if value == 0 or None
    """
    if value and value > 0:
        return {"some": value}
    else:
        return {"none": []}

async def migrate_fixed():
    # Connect to SQLite
    sqlite_conn = sqlite3.connect('knowledge.db')
    sqlite_conn.row_factory = sqlite3.Row
    
    # Connect to SpacetimeDB
    stdb = StdbClient()
    
    print("=== Migrating work_plans and work_items (FIXED VERSION) ===\n")
    
    # First, check if tables exist and have data
    print("1. Checking current state...")
    try:
        plans_count = await stdb.query("SELECT COUNT(*) as count FROM work_plans")
        items_count = await stdb.query("SELECT COUNT(*) as count FROM work_items")
        print(f"   Current in SpacetimeDB: work_plans={plans_count[0]['count'] if plans_count else 0}, work_items={items_count[0]['count'] if items_count else 0}")
    except Exception as e:
        print(f"   Error checking: {e}")
    
    # Migrate work_plans using import_work_plan reducer
    print("\n2. Migrating work_plans...")
    cursor = sqlite_conn.cursor()
    cursor.execute("""
        SELECT id, agent_id, conversation_id, parent_plan_id, title, status,
               created_at, updated_at, completed_at
        FROM work_plans
    """)
    rows = cursor.fetchall()
    
    print(f"   Found {len(rows)} work plans in SQLite")
    
    migrated = 0
    failed = 0
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
        
        # Build arguments for import_work_plan reducer with correct sum type for optional completedAt
        args = [
            plan_id,
            agent_id,
            conversation_id,
            parent_plan_id,
            title,
            status,
            created_at,
            updated_at,
            make_optional_u64(completed_at),  # Sum type for optional U64
        ]
        
        # Try calling the reducer
        success = await stdb.call_reducer("import_work_plan", args)
        if success:
            migrated += 1
            if migrated % 20 == 0:
                print(f"   Migrated {migrated} work plans...")
        else:
            failed += 1
            if failed <= 5:  # Show first 5 failures
                print(f"   Failed to migrate work plan {plan_id}")
    
    print(f"   Result: {migrated} migrated, {failed} failed")
    
    # Migrate work_items using import_work_item reducer
    print("\n3. Migrating work_items...")
    # Note: SQLite doesn't have description column, but SpacetimeDB does
    cursor.execute("""
        SELECT id, plan_id, title, status, ordinal, context_snapshot,
               notes, files_changed, started_at, completed_at,
               created_at, updated_at
        FROM work_items
    """)
    rows = cursor.fetchall()
    
    print(f"   Found {len(rows)} work items in SQLite")
    
    migrated = 0
    failed = 0
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
        description = ""  # SQLite doesn't have this column
        
        # Build arguments for import_work_item reducer
        # Note: startedAt and completedAt are optional U64, need sum type
        args = [
            item_id,
            plan_id,
            title,
            status,
            ordinal,
            context_snapshot,
            notes,
            files_changed,
            make_optional_u64(started_at),   # Optional U64
            make_optional_u64(completed_at), # Optional U64
            created_at,
            updated_at,
            description,  # Has default value in schema
        ]
        
        # Try calling the reducer
        success = await stdb.call_reducer("import_work_item", args)
        if success:
            migrated += 1
            if migrated % 20 == 0:
                print(f"   Migrated {migrated} work items...")
        else:
            failed += 1
            if failed <= 5:
                print(f"   Failed to migrate work item {item_id}")
    
    print(f"   Result: {migrated} migrated, {failed} failed")
    
    print("\n=== Migration complete ===")
    
    # Verify migration
    print("\n4. Verifying migration...")
    try:
        plans_count = await stdb.query("SELECT COUNT(*) as count FROM work_plans")
        items_count = await stdb.query("SELECT COUNT(*) as count FROM work_items")
        
        print(f"   work_plans: {plans_count[0]['count'] if plans_count else 0} rows")
        print(f"   work_items: {items_count[0]['count'] if items_count else 0} rows")
        
        # Show a sample
        if plans_count and plans_count[0]['count'] > 0:
            sample = await stdb.query("SELECT id, title, status FROM work_plans LIMIT 3")
            print(f"   Sample work plans:")
            for plan in sample:
                print(f"     - {plan['id']}: {plan['title']} ({plan['status']})")
        
    except Exception as e:
        print(f"   Error verifying: {e}")
    
    await stdb.close()
    sqlite_conn.close()

if __name__ == "__main__":
    asyncio.run(migrate_fixed())