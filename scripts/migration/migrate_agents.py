#!/usr/bin/env python3
"""Migrate agents from SQLite to SpacetimeDB."""

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
        
        # Call add_agent reducer
        success = await stdb.call_reducer("add_agent", [
            agent_id,
            agent['name'],
            agent['display_name'],
            agent['system_prompt'] or "",
            agent['model'],
            agent['utility_model'] or "claude-sonnet-4-6",
            tools,
            bool(agent['is_default']),
        ])
        
        if success:
            print(f"Migrated agent {agent_name} ({agent_id}) to SpacetimeDB")
            
            # Migrate workspace mounts
            cursor.execute("""
                SELECT host_path, mount_name, container_path, readonly
                FROM agent_workspace_mounts
                WHERE agent_id = ?
            """, (agent_id,))
            
            mounts = cursor.fetchall()
            for mount in mounts:
                # Call add_agent_workspace_mount reducer if it exists
                mount_success = await stdb.call_reducer("add_agent_workspace_mount", [
                    agent_id,
                    mount['host_path'],
                    mount['mount_name'],
                    mount['container_path'] or "",
                    bool(mount['readonly']),
                ])
                if mount_success:
                    print(f"  - Migrated mount: {mount['mount_name']}")
                else:
                    print(f"  - Failed to migrate mount: {mount['mount_name']} (reducer might not exist)")
            
            # Migrate channels
            cursor.execute("""
                SELECT channel, enabled, sandbox_override
                FROM agent_channels
                WHERE agent_id = ?
            """, (agent_id,))
            
            channels = cursor.fetchall()
            for channel in channels:
                # Call add_agent_channel reducer if it exists
                channel_success = await stdb.call_reducer("add_agent_channel", [
                    agent_id,
                    channel['channel'],
                    bool(channel['enabled']),
                    channel['sandbox_override'] or "",
                ])
                if channel_success:
                    print(f"  - Migrated channel: {channel['channel']}")
                else:
                    print(f"  - Failed to migrate channel: {channel['channel']} (reducer might not exist)")
        else:
            print(f"Failed to migrate agent {agent_name} ({agent_id})")
    
    sqlite_conn.close()
    await stdb.close()
    print("Migration complete!")

if __name__ == "__main__":
    asyncio.run(migrate_agents())