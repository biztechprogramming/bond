#!/usr/bin/env python3
"""Migrate agents from SQLite to SpacetimeDB using direct SQL."""

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
    
    # Get all agents from SQLite
    cursor = sqlite_conn.cursor()
    cursor.execute("""
        SELECT id, name, display_name, system_prompt, model, utility_model, 
               tools, sandbox_image, max_iterations, auto_rag, auto_rag_limit,
               is_default, is_active, created_at
        FROM agents
    """)
    
    agents = cursor.fetchall()
    print(f"Found {len(agents)} agents in SQLite")
    
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
        
        # Build SQL INSERT statement
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
            {1 if agent['is_active'] else 0},
            {1 if agent['is_default'] else 0},
            {created_at_ms}
        )
        """
        
        # Execute SQL
        result = await stdb.query(sql)
        if result is not None:  # Successful query returns empty list
            print(f"Migrated agent {agent_name} ({agent_id}) to SpacetimeDB")
            migrated_count += 1
            
            # Migrate workspace mounts
            cursor.execute("""
                SELECT host_path, mount_name, container_path, readonly
                FROM agent_workspace_mounts
                WHERE agent_id = ?
            """, (agent_id,))
            
            mounts = cursor.fetchall()
            for mount in mounts:
                # Try to insert mount
                mount_sql = f"""
                INSERT INTO agent_workspace_mounts (agent_id, host_path, mount_name, container_path, readonly)
                VALUES (
                    '{agent_id}',
                    '{mount['host_path'].replace("'", "''")}',
                    '{mount['mount_name'].replace("'", "''")}',
                    '{mount['container_path'].replace("'", "''") if mount['container_path'] else ''}',
                    {1 if mount['readonly'] else 0}
                )
                """
                try:
                    await stdb.query(mount_sql)
                    print(f"  - Migrated mount: {mount['mount_name']}")
                except Exception as e:
                    print(f"  - Failed to migrate mount {mount['mount_name']}: {e}")
            
            # Migrate channels (if table exists)
            cursor.execute("""
                SELECT channel, enabled, sandbox_override
                FROM agent_channels
                WHERE agent_id = ?
            """, (agent_id,))
            
            channels = cursor.fetchall()
            for channel in channels:
                # Try to insert channel
                channel_sql = f"""
                INSERT INTO agent_channels (agent_id, channel, enabled, sandbox_override)
                VALUES (
                    '{agent_id}',
                    '{channel['channel']}',
                    {1 if channel['enabled'] else 0},
                    '{channel['sandbox_override'] or ''}'
                )
                """
                try:
                    await stdb.query(channel_sql)
                    print(f"  - Migrated channel: {channel['channel']}")
                except Exception as e:
                    print(f"  - Failed to migrate channel {channel['channel']}: {e}")
        else:
            print(f"Failed to migrate agent {agent_name} ({agent_id})")
    
    sqlite_conn.close()
    await stdb.close()
    print(f"\nMigration complete! Migrated {migrated_count} agents.")

if __name__ == "__main__":
    asyncio.run(migrate_agents())