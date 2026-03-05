#!/usr/bin/env python3
"""Migrate just work_plans and work_items."""

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

async def migrate_work_tables():
    # Connect to SQLite
    sqlite_conn = sqlite3.connect('knowledge.db')
    sqlite_conn.row_factory = sqlite3.Row
    
    # Connect to SpacetimeDB
    stdb = StdbClient()
    
    print("=== Migrating work_plans and work_items ===")
    
    # First, clear any existing data (in case of partial migration)
    try:
        await stdb.query("DELETE FROM work_items")
        await stdb.query("DELETE FROM work_plans")
        print("Cleared existing work data")
    except:
        pass
    
    # Migrate work_plans
    print("\nMigrating work_plans...")
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
        
        # For optional columns, omit them if they're 0 (not set)
        if completed_at > 0:
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
                    {completed_at}
                )
            """)
        else:
            await stdb.query(f"""
                INSERT INTO work_plans (
                    id, agent_id, conversation_id, parent_plan_id, title, status,
                    created_at, updated_at
                ) VALUES (
                    '{plan_id}',
                    '{agent_id}',
                    '{conversation_id}',
                    '{parent_plan_id}',
                    '{title}',
                    '{status}',
                    {created_at},
                    {updated_at}
                )
            """)
        migrated += 1
        if migrated % 20 == 0:
            print(f"  Migrated {migrated} work plans...")
    print(f"  Migrated {migrated} work plans total")
    
    # Migrate work_items
    print("\nMigrating work_items...")
    cursor.execute("""
        SELECT id, plan_id, title, status, ordinal, context_snapshot,
               notes, files_changed, started_at, completed_at,
               created_at, updated_at
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
        
        # Build dynamic INSERT based on optional columns
        columns = ["id", "plan_id", "title", "status", "ordinal", "context_snapshot",
                  "notes", "files_changed", "created_at", "updated_at", "description"]
        values = [f"'{item_id}'", f"'{plan_id}'", f"'{title}'", f"'{status}'", 
                 str(ordinal), f"'{context_snapshot}'", f"'{notes}'", 
                 f"'{files_changed}'", str(created_at), str(updated_at), "''"]
        
        if started_at > 0:
            columns.append("started_at")
            values.append(str(started_at))
        if completed_at > 0:
            columns.append("completed_at")
            values.append(str(completed_at))
        
        columns_str = ", ".join(columns)
        values_str = ", ".join(values)
        
        await stdb.query(f"""
            INSERT INTO work_items ({columns_str})
            VALUES ({values_str})
        """)
        migrated += 1
        if migrated % 20 == 0:
            print(f"  Migrated {migrated} work items...")
    print(f"  Migrated {migrated} work items total")
    
    print("\n=== Migration complete ===")
    
    await stdb.close()
    sqlite_conn.close()

if __name__ == "__main__":
    asyncio.run(migrate_work_tables())