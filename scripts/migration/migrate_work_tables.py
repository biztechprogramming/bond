#!/usr/bin/env python3
"""Migrate work_plans and work_items with correct optional column syntax."""

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

def make_optional_u64_sql(value):
    """Convert a U64 value to SpacetimeDB optional sum type SQL syntax.
    
    Returns:
        - '(some: value)' if value > 0
        - '(none: ())' if value == 0 or None
    """
    if value and value > 0:
        return f'(some: {value})'
    else:
        return '(none: ())'

async def migrate_work_tables_fixed():
    # Connect to SQLite
    sqlite_conn = sqlite3.connect('knowledge.db')
    sqlite_conn.row_factory = sqlite3.Row
    
    # Connect to SpacetimeDB
    stdb = StdbClient()
    
    print("=== Migrating work_plans and work_items (FIXED with correct optional syntax) ===")
    
    # First, clear any existing data (in case of partial migration)
    try:
        await stdb.query("DELETE FROM work_items")
        await stdb.query("DELETE FROM work_plans")
        print("Cleared existing work data")
    except Exception as e:
        print(f"Note: Could not clear existing data: {e}")
    
    # Migrate work_plans
    print("\n1. Migrating work_plans...")
    cursor = sqlite_conn.cursor()
    cursor.execute("""
        SELECT id, agent_id, conversation_id, parent_plan_id, title, status,
               created_at, updated_at, completed_at
        FROM work_plans
    """)
    rows = cursor.fetchall()
    
    print(f"  Found {len(rows)} work plans in SQLite")
    
    migrated = 0
    failed = 0
    for row in rows:
        try:
            plan_id = row["id"]
            agent_id = row["agent_id"]
            conversation_id = escape_sql(row["conversation_id"])
            parent_plan_id = escape_sql(row["parent_plan_id"])
            title = escape_sql(row["title"])
            status = escape_sql(row["status"]) or "active"
            created_at = int(datetime.fromisoformat(row["created_at"]).timestamp() * 1000) if row["created_at"] else int(time.time() * 1000)
            updated_at = int(datetime.fromisoformat(row["updated_at"]).timestamp() * 1000) if row["updated_at"] else created_at
            completed_at = int(datetime.fromisoformat(row["completed_at"]).timestamp() * 1000) if row["completed_at"] else 0
            
            # Use correct optional syntax for completed_at
            completed_at_sql = make_optional_u64_sql(completed_at)
            
            await stdb.query(f"""
                INSERT INTO work_plans (
                    id, agent_id, conversation_id, parent_plan_id, title, status,
                    created_at, updated_at, completed_at
                ) VALUES (
                    '{plan_id}',
                    '{agent_id}',
                    '{conversation_id}',
                    '{parent_plan_id}',
                    '{title}',
                    '{status}',
                    {created_at},
                    {updated_at},
                    '{completed_at_sql}'
                )
            """)
            migrated += 1
            if migrated % 20 == 0:
                print(f"  Migrated {migrated} work plans...")
        except Exception as e:
            failed += 1
            if failed <= 5:  # Show first 5 failures
                print(f"  Failed to migrate work plan {row.get('id', 'unknown')}: {e}")
    
    print(f"  Result: {migrated} migrated, {failed} failed")
    
    # Migrate work_items
    print("\n2. Migrating work_items...")
    cursor.execute("""
        SELECT id, plan_id, title, status, ordinal, context_snapshot,
               notes, files_changed, started_at, completed_at,
               created_at, updated_at
        FROM work_items
    """)
    rows = cursor.fetchall()
    
    print(f"  Found {len(rows)} work items in SQLite")
    
    migrated = 0
    failed = 0
    for row in rows:
        try:
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
            
            # Use correct optional syntax for started_at and completed_at
            started_at_sql = make_optional_u64_sql(started_at)
            completed_at_sql = make_optional_u64_sql(completed_at)
            
            await stdb.query(f"""
                INSERT INTO work_items (
                    id, plan_id, title, status, ordinal, context_snapshot,
                    notes, files_changed, started_at, completed_at,
                    created_at, updated_at, description
                ) VALUES (
                    '{item_id}',
                    '{plan_id}',
                    '{title}',
                    '{status}',
                    {ordinal},
                    '{context_snapshot}',
                    '{notes}',
                    '{files_changed}',
                    '{started_at_sql}',
                    '{completed_at_sql}',
                    {created_at},
                    {updated_at},
                    '{description}'
                )
            """)
            migrated += 1
            if migrated % 20 == 0:
                print(f"  Migrated {migrated} work items...")
        except Exception as e:
            failed += 1
            if failed <= 5:  # Show first 5 failures
                print(f"  Failed to migrate work item {row.get('id', 'unknown')}: {e}")
    
    print(f"  Result: {migrated} migrated, {failed} failed")
    
    # Verify migration
    print("\n3. Verifying migration...")
    try:
        plans_count = await stdb.query("SELECT COUNT(*) as count FROM work_plans")
        items_count = await stdb.query("SELECT COUNT(*) as count FROM work_items")
        print(f"  In SpacetimeDB: work_plans={plans_count[0]['count'] if plans_count else 0}, work_items={items_count[0]['count'] if items_count else 0}")
    except Exception as e:
        print(f"  Error verifying: {e}")
    
    print("\n=== Migration complete ===")
    
    await stdb.close()
    sqlite_conn.close()

if __name__ == "__main__":
    asyncio.run(migrate_work_tables_fixed())