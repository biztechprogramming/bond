#!/usr/bin/env python3
"""Migrate agents from SQLite to SpacetimeDB using direct SQL - FIXED VERSION."""

import asyncio
import json
import sqlite3
from datetime import datetime
from backend.app.core.spacetimedb import StdbClient

async def migrate_agents():
    # Connect to SQLite
    sqlite_conn = sqlite3.connect('knowledge.db')
    sqlite_conn.row_factory = sqlite3.Row
    
    # Connect to SpacetimeDB
    stdb = StdbClient()
    
    # First, let's check the schema of agent_workspace_mounts by trying to insert a test row
    print("Checking agent_workspace_mounts schema...")
    test_mount_sql = """
    INSERT INTO agent_workspace_mounts (agent_id, host_path, mount_name, container_path, readonly, id)
    VALUES (
        'test-agent',
        '/tmp',
        'test-mount',
        '/workspace/test',
        false,
        'test-mount-id'
    )
    """
    try:
        await stdb.query(test_mount_sql)
        print("Test mount insert succeeded (or at least didn't fail on column count)")
        # Clean up
        await stdb.query("DELETE FROM agent_workspace_mounts WHERE id = 'test-mount-id'")
    except Exception as e:
        print(f"Test mount insert error: {e}")
    
    # Get all agents from SQLite
    cursor = sqlite_conn.cursor()
    cursor.execute("""
        SELECT id, name, display_name, system_prompt, model, utility_model, 
               tools, sandbox_image, max_iterations, auto_rag, auto_rag_limit,
               is_default, is_active, created_at
        FROM agents
    """)
    
    agents = cursor.fetchall()
    print(f"\nFound {len(agents)} agents in SQLite")
    
    migrated_count = 0
    for agent in agents:
        agent_id = agent['id']
        agent_name = agent['name']
        
        # Check if agent already exists in SpacetimeDB
        existing = await stdb.query(f"SELECT id FROM agents WHERE id = '{agent_id}'")
        if existing:
            print(f"Agent {agent_name} ({agent_id}) already exists in SpacetimeDB, skipping")
            continue
        
        # Convert tools to JSON string if needed
        tools = agent['tools']
        if not isinstance(tools, str):
            tools = json.dumps(tools)
        
        # Convert timestamps
        created_at = agent['created_at']
        if isinstance(created_at, str):
            # Try to parse timestamp string
            try:
                dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                created_at_ms = int(dt.timestamp() * 1000)
            except:
                created_at_ms = int(datetime.now().timestamp() * 1000)
        else:
            created_at_ms = int(datetime.now().timestamp() * 1000)
        
        # Build SQL INSERT statement with proper boolean literals
        sql = f"""
        INSERT INTO agents (
            id, name, display_name, system_prompt, model, utility_model,
            tools, sandbox_image, max_iterations, is_active, is_default, created_at
        ) VALUES (
            '{agent_id}',
            '{agent['name'].replace("'", "''")}',
            '{agent['display_name'].replace("'", "''")}',
            '{agent['system_prompt'].replace("'", "''") if agent['system_prompt'] else ''}',
            '{agent['model']}',
            '{agent['utility_model'] or 'claude-sonnet-4-6'}',
            '{tools.replace("'", "''")}',
            '{agent['sandbox_image'] or ''}',
            {agent['max_iterations'] or 25},
            {'true' if agent['is_active'] else 'false'},
            {'true' if agent['is_default'] else 'false'},
            {created_at_ms}
        )
        """
        
        # Execute SQL
        try:
            result = await stdb.query(sql)
            print(f"Migrated agent {agent_name} ({agent_id}) to SpacetimeDB")
            migrated_count += 1
            
            # Migrate workspace mounts
            cursor.execute("""
                SELECT id, host_path, mount_name, container_path, readonly
                FROM agent_workspace_mounts
                WHERE agent_id = ?
            """, (agent_id,))
            
            mounts = cursor.fetchall()
            for mount in mounts:
                # Try to insert mount with id
                mount_sql = f"""
                INSERT INTO agent_workspace_mounts (id, agent_id, host_path, mount_name, container_path, readonly)
                VALUES (
                    '{mount['id']}',
                    '{agent_id}',
                    '{mount['host_path'].replace("'", "''")}',
                    '{mount['mount_name'].replace("'", "''")}',
                    '{mount['container_path'].replace("'", "''") if mount['container_path'] else ''}',
                    {'true' if mount['readonly'] else 'false'}
                )
                """
                try:
                    await stdb.query(mount_sql)
                    print(f"  - Migrated mount: {mount['mount_name']}")
                except Exception as e:
                    print(f"  - Failed to migrate mount {mount['mount_name']}: {e}")
            
            # Skip channels for now since table doesn't exist
            
        except Exception as e:
            print(f"Failed to migrate agent {agent_name} ({agent_id}): {e}")
    
    sqlite_conn.close()
    await stdb.close()
    print(f"\nMigration complete! Migrated {migrated_count} agents.")
    
    # Final check
    print("\nFinal check of agents in SpacetimeDB:")
    stdb2 = StdbClient()
    rows = await stdb2.query("SELECT id, name, is_default FROM agents")
    print(f"Found {len(rows)} agents:")
    for row in rows:
        print(f"  - {row['id']}: {row['name']} (default: {row['is_default']})")
    await stdb2.close()

if __name__ == "__main__":
    asyncio.run(migrate_agents())