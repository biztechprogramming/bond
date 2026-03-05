#!/usr/bin/env python3
"""Migrate critical data from SQLite to SpacetimeDB."""

import asyncio
import json
import sqlite3
import time
from datetime import datetime
from backend.app.core.spacetimedb import StdbClient

async def migrate_critical():
    # Connect to SQLite
    sqlite_conn = sqlite3.connect('knowledge.db')
    sqlite_conn.row_factory = sqlite3.Row
    
    # Connect to SpacetimeDB
    stdb = StdbClient()
    
    print("=== Migrating critical data from SQLite to SpacetimeDB ===")
    
    # 1. Migrate agents
    print("\n1. Migrating agents...")
    cursor = sqlite_conn.cursor()
    cursor.execute("""
        SELECT id, name, display_name, system_prompt, model, utility_model, 
               tools, sandbox_image, max_iterations, auto_rag, auto_rag_limit,
               is_default, is_active, created_at
        FROM agents
    """)
    agents = cursor.fetchall()
    
    for agent in agents:
        agent_id = agent["id"]
        name = agent["name"]
        display_name = agent["display_name"]
        system_prompt = agent["system_prompt"] or ""
        model = agent["model"]
        utility_model = agent["utility_model"] or "claude-sonnet-4-6"
        tools = agent["tools"] or "[]"
        sandbox_image = agent["sandbox_image"] or ""
        max_iterations = agent["max_iterations"] or 25
        is_default = bool(agent["is_default"])
        is_active = bool(agent["is_active"])
        created_at = int(datetime.fromisoformat(agent["created_at"]).timestamp() * 1000) if agent["created_at"] else int(time.time() * 1000)
        
        # Check if agent already exists
        existing = await stdb.query(f"SELECT id FROM agents WHERE id = '{agent_id}'")
        if existing:
            print(f"  Agent '{name}' already exists, skipping")
            continue
        
        # Insert agent
        await stdb.query(f"""
            INSERT INTO agents (
                id, name, display_name, system_prompt, model, utility_model,
                tools, sandbox_image, max_iterations, is_active, is_default, created_at
            ) VALUES (
                '{agent_id}',
                '{name}',
                '{display_name}',
                '{system_prompt}',
                '{model}',
                '{utility_model}',
                '{tools}',
                '{sandbox_image}',
                {max_iterations},
                {str(is_active).lower()},
                {str(is_default).lower()},
                {created_at}
            )
        """)
        print(f"  Migrated agent: {name}")
    
    # 2. Migrate agent_workspace_mounts
    print("\n2. Migrating agent_workspace_mounts...")
    cursor.execute("SELECT id, agent_id, host_path, mount_name, container_path, readonly FROM agent_workspace_mounts")
    mounts = cursor.fetchall()
    
    for mount in mounts:
        mount_id = mount["id"]
        agent_id = mount["agent_id"]
        host_path = mount["host_path"]
        mount_name = mount["mount_name"]
        container_path = mount["container_path"] or f"/workspace/{mount_name}"
        readonly = bool(mount["readonly"])
        
        # Check if mount already exists
        existing = await stdb.query(f"SELECT id FROM agent_workspace_mounts WHERE id = '{mount_id}'")
        if existing:
            print(f"  Mount '{mount_name}' already exists, skipping")
            continue
        
        # Insert mount
        await stdb.query(f"""
            INSERT INTO agent_workspace_mounts (
                id, agent_id, host_path, mount_name, container_path, readonly
            ) VALUES (
                '{mount_id}',
                '{agent_id}',
                '{host_path}',
                '{mount_name}',
                '{container_path}',
                {str(readonly).lower()}
            )
        """)
        print(f"  Migrated mount: {mount_name} for agent {agent_id}")
    
    # 3. Migrate agent_channels
    print("\n3. Migrating agent_channels...")
    cursor.execute("SELECT id, agent_id, channel, sandbox_override, enabled, created_at FROM agent_channels")
    channels = cursor.fetchall()
    
    for channel in channels:
        channel_id = channel["id"]
        agent_id = channel["agent_id"]
        channel_name = channel["channel"]
        sandbox_override = channel["sandbox_override"] or ""
        enabled = bool(channel["enabled"])
        created_at = int(datetime.fromisoformat(channel["created_at"]).timestamp() * 1000) if channel["created_at"] else int(time.time() * 1000)
        
        # Check if channel already exists
        existing = await stdb.query(f"SELECT id FROM agent_channels WHERE id = '{channel_id}'")
        if existing:
            print(f"  Channel '{channel_name}' already exists, skipping")
            continue
        
        # Insert channel
        await stdb.query(f"""
            INSERT INTO agent_channels (id, agent_id, channel, sandbox_override, enabled, created_at)
            VALUES (
                '{channel_id}',
                '{agent_id}',
                '{channel_name}',
                '{sandbox_override}',
                {str(enabled).lower()},
                {created_at}
            )
        """)
        print(f"  Migrated channel: {channel_name} for agent {agent_id}")
    
    # 4. Migrate settings
    print("\n4. Migrating settings...")
    cursor.execute("SELECT key, value, key_type, created_at, updated_at FROM settings")
    settings = cursor.fetchall()
    
    for setting in settings:
        key = setting["key"]
        value = setting["value"]
        key_type = setting["key_type"] or "api_key"
        created_at = int(datetime.fromisoformat(setting["created_at"]).timestamp() * 1000) if setting["created_at"] else int(time.time() * 1000)
        updated_at = int(datetime.fromisoformat(setting["updated_at"]).timestamp() * 1000) if setting["updated_at"] else created_at
        
        # Check if setting already exists
        existing = await stdb.query(f"SELECT key FROM settings WHERE key = '{key}'")
        if existing:
            print(f"  Setting '{key}' already exists, skipping")
            continue
        
        # Insert setting
        await stdb.query(f"""
            INSERT INTO settings (key, value, key_type, created_at, updated_at)
            VALUES (
                '{key}',
                '{value}',
                '{key_type}',
                {created_at},
                {updated_at}
            )
        """)
        print(f"  Migrated setting: {key}")
    
    # 5. Migrate conversations
    print("\n5. Migrating conversations...")
    cursor.execute("""
        SELECT id, agent_id, channel, title, is_active, message_count, 
               rolling_summary, summary_covers_to, recent_tools_used,
               created_at, updated_at
        FROM conversations
    """)
    conversations = cursor.fetchall()
    
    for conv in conversations:
        conv_id = conv["id"]
        
        # Check if conversation already exists
        existing = await stdb.query(f"SELECT id FROM conversations WHERE id = '{conv_id}'")
        if existing:
            print(f"  Conversation '{conv_id}' already exists, skipping")
            continue
        
        # Prepare data
        agent_id = conv["agent_id"]
        channel = conv["channel"]
        title = conv["title"] or ""
        is_active = bool(conv["is_active"])
        message_count = conv["message_count"] or 0
        rolling_summary = conv["rolling_summary"] or ""
        summary_coversto = conv["summary_covers_to"] or 0
        recent_tools_used = conv["recent_tools_used"] or "[]"
        created_at = int(datetime.fromisoformat(conv["created_at"]).timestamp() * 1000) if conv["created_at"] else int(time.time() * 1000)
        updated_at = int(datetime.fromisoformat(conv["updated_at"]).timestamp() * 1000) if conv["updated_at"] else created_at
        
        # Insert conversation
        await stdb.query(f"""
            INSERT INTO conversations (
                id, agent_id, channel, title, is_active, message_count,
                rolling_summary, summary_coversto, recent_tools_used,
                created_at, updated_at
            ) VALUES (
                '{conv_id}',
                '{agent_id}',
                '{channel}',
                '{title}',
                {str(is_active).lower()},
                {message_count},
                '{rolling_summary}',
                {summary_coversto},
                '{recent_tools_used}',
                {created_at},
                {updated_at}
            )
        """)
        print(f"  Migrated conversation: {conv_id} ({title})")
    
    # 6. Migrate conversation_messages (limit to most recent to avoid timeouts)
    print("\n6. Migrating recent conversation_messages (last 1000)...")
    cursor.execute("""
        SELECT id, conversation_id, role, content, tool_calls, tool_call_id,
               token_count, status, created_at
        FROM conversation_messages
        ORDER BY created_at DESC
        LIMIT 1000
    """)
    messages = cursor.fetchall()
    
    migrated_count = 0
    for msg in messages:
        msg_id = msg["id"]
        
        # Check if message already exists
        existing = await stdb.query(f"SELECT id FROM conversation_messages WHERE id = '{msg_id}'")
        if existing:
            migrated_count += 1
            continue
        
        # Prepare data
        conversation_id = msg["conversation_id"]
        role = msg["role"]
        content = msg["content"] or ""
        tool_calls = msg["tool_calls"] or ""
        tool_call_id = msg["tool_call_id"] or ""
        token_count = msg["token_count"] or 0
        status = msg["status"] or "delivered"
        created_at = int(datetime.fromisoformat(msg["created_at"]).timestamp() * 1000) if msg["created_at"] else int(time.time() * 1000)
        
        # Insert message
        await stdb.query(f"""
            INSERT INTO conversation_messages (
                id, conversation_id, role, content, tool_calls, tool_call_id,
                token_count, status, created_at
            ) VALUES (
                '{msg_id}',
                '{conversation_id}',
                '{role}',
                '{content}',
                '{tool_calls}',
                '{tool_call_id}',
                {token_count},
                '{status}',
                {created_at}
            )
        """)
        migrated_count += 1
        if migrated_count % 100 == 0:
            print(f"  Migrated {migrated_count} messages...")
    
    print(f"  Migrated {migrated_count} conversation messages total")
    
    # 7. Migrate work_plans
    print("\n7. Migrating work_plans...")
    cursor.execute("""
        SELECT id, agent_id, conversation_id, parent_plan_id, title, status,
               created_at, updated_at, completed_at
        FROM work_plans
    """)
    work_plans = cursor.fetchall()
    
    for plan in work_plans:
        plan_id = plan["id"]
        
        # Check if work plan already exists
        existing = await stdb.query(f"SELECT id FROM work_plans WHERE id = '{plan_id}'")
        if existing:
            print(f"  Work plan '{plan_id}' already exists, skipping")
            continue
        
        # Prepare data
        agent_id = plan["agent_id"]
        conversation_id = plan["conversation_id"] or ""
        parent_plan_id = plan["parent_plan_id"] or ""
        title = plan["title"] or ""
        status = plan["status"] or "active"
        created_at = int(datetime.fromisoformat(plan["created_at"]).timestamp() * 1000) if plan["created_at"] else int(time.time() * 1000)
        updated_at = int(datetime.fromisoformat(plan["updated_at"]).timestamp() * 1000) if plan["updated_at"] else created_at
        completed_at = int(datetime.fromisoformat(plan["completed_at"]).timestamp() * 1000) if plan["completed_at"] else 0
        
        # Insert work plan
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
                {completed_at if completed_at > 0 else 'NULL'}
            )
        """)
        print(f"  Migrated work plan: {plan_id} ({title})")
    
    # 8. Migrate work_items
    print("\n8. Migrating work_items...")
    cursor.execute("""
        SELECT id, plan_id, title, status, ordinal, context_snapshot,
               notes, files_changed, started_at, completed_at,
               created_at, updated_at
        FROM work_items
    """)
    work_items = cursor.fetchall()
    
    for item in work_items:
        item_id = item["id"]
        
        # Check if work item already exists
        existing = await stdb.query(f"SELECT id FROM work_items WHERE id = '{item_id}'")
        if existing:
            print(f"  Work item '{item_id}' already exists, skipping")
            continue
        
        # Prepare data
        plan_id = item["plan_id"]
        title = item["title"] or ""
        status = item["status"] or "new"
        ordinal = item["ordinal"] or 0
        context_snapshot = item["context_snapshot"] or "{}"
        notes = item["notes"] or "[]"
        files_changed = item["files_changed"] or "[]"
        started_at = int(datetime.fromisoformat(item["started_at"]).timestamp() * 1000) if item["started_at"] else 0
        completed_at = int(datetime.fromisoformat(item["completed_at"]).timestamp() * 1000) if item["completed_at"] else 0
        created_at = int(datetime.fromisoformat(item["created_at"]).timestamp() * 1000) if item["created_at"] else int(time.time() * 1000)
        updated_at = int(datetime.fromisoformat(item["updated_at"]).timestamp() * 1000) if item["updated_at"] else created_at
        
        # Insert work item
        await stdb.query(f"""
            INSERT INTO work_items (
                id, plan_id, title, status, ordinal, context_snapshot,
                notes, files_changed, started_at, completed_at,
                created_at, updated_at
            ) VALUES (
                '{item_id}',
                '{plan_id}',
                '{title}',
                '{status}',
                {ordinal},
                '{context_snapshot}',
                '{notes}',
                '{files_changed}',
                {started_at if started_at > 0 else 'NULL'},
                {completed_at if completed_at > 0 else 'NULL'},
                {created_at},
                {updated_at}
            )
        """)
        print(f"  Migrated work item: {item_id} ({title})")
    
    print("\n=== Migration complete ===")
    
    # Print summary
    print("\nSummary:")
    print(f"- Agents: {len(agents)} rows")
    print(f"- Agent workspace mounts: {len(mounts)} rows")
    print(f"- Agent channels: {len(channels)} rows")
    print(f"- Settings: {len(settings)} rows")
    print(f"- Conversations: {len(conversations)} rows")
    print(f"- Conversation messages: {migrated_count} rows (of {len(messages)} recent)")
    print(f"- Work plans: {len(work_plans)} rows")
    print(f"- Work items: {len(work_items)} rows")
    
    await stdb.close()
    sqlite_conn.close()

if __name__ == "__main__":
    asyncio.run(migrate_critical())