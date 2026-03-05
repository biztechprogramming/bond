#!/usr/bin/env python3
"""Migrate ALL data from SQLite to SpacetimeDB."""

import asyncio
import json
import sqlite3
import time
from datetime import datetime
from backend.app.core.spacetimedb import StdbClient

async def migrate_all():
    # Connect to SQLite
    sqlite_conn = sqlite3.connect('knowledge.db')
    sqlite_conn.row_factory = sqlite3.Row
    
    # Connect to SpacetimeDB
    stdb = StdbClient()
    
    print("=== Migrating ALL data from SQLite to SpacetimeDB ===")
    
    # 1. Migrate providers
    print("\n1. Migrating providers...")
    cursor = sqlite_conn.cursor()
    cursor.execute("SELECT id, name, api_base, auth_type, supports_streaming FROM providers")
    providers = cursor.fetchall()
    
    for provider in providers:
        provider_id = provider["id"]
        name = provider["name"]
        api_base = provider["api_base"] or ""
        auth_type = provider["auth_type"] or "bearer"
        supports_streaming = bool(provider["supports_streaming"])
        
        # Insert provider
        await stdb.query(f"""
            INSERT INTO providers (id, name, api_base, auth_type, supports_streaming)
            VALUES (
                '{provider_id}',
                '{name}',
                '{api_base}',
                '{auth_type}',
                {str(supports_streaming).lower()}
            )
        """)
        print(f"  Migrated provider: {name}")
    
    # 2. Migrate provider_api_keys
    print("\n2. Migrating provider_api_keys...")
    cursor.execute("SELECT id, provider_id, encrypted_key, key_type, created_at, updated_at FROM provider_api_keys")
    api_keys = cursor.fetchall()
    
    for key in api_keys:
        key_id = key["id"]
        provider_id = key["provider_id"]
        encrypted_key = key["encrypted_key"] or ""
        key_type = key["key_type"] or "api_key"
        created_at = int(datetime.fromisoformat(key["created_at"]).timestamp() * 1000) if key["created_at"] else int(time.time() * 1000)
        updated_at = int(datetime.fromisoformat(key["updated_at"]).timestamp() * 1000) if key["updated_at"] else created_at
        
        # Insert provider API key
        await stdb.query(f"""
            INSERT INTO provider_api_keys (id, provider_id, encrypted_key, key_type, created_at, updated_at)
            VALUES (
                '{key_id}',
                '{provider_id}',
                '{encrypted_key}',
                '{key_type}',
                {created_at},
                {updated_at}
            )
        """)
        print(f"  Migrated API key for provider: {provider_id}")
    
    # 3. Migrate llm_models (from SQLite - but SpacetimeDB might have more from sync)
    print("\n3. Migrating llm_models...")
    cursor.execute("SELECT id, provider_id, name, family, context_window, supports_function_calling, supports_vision, max_output_tokens, created_at FROM llm_models")
    models = cursor.fetchall()
    
    for model in models:
        model_id = model["id"]
        
        # Check if model already exists
        existing = await stdb.query(f"SELECT id FROM llm_models WHERE id = '{model_id}'")
        if existing:
            continue
        
        # Prepare data
        provider_id = model["provider_id"]
        name = model["name"]
        family = model["family"] or ""
        context_window = model["context_window"] or 0
        supports_function_calling = bool(model["supports_function_calling"])
        supports_vision = bool(model["supports_vision"])
        max_output_tokens = model["max_output_tokens"] or 0
        created_at = int(datetime.fromisoformat(model["created_at"]).timestamp() * 1000) if model["created_at"] else int(time.time() * 1000)
        
        # Insert model
        await stdb.query(f"""
            INSERT INTO llm_models (
                id, provider_id, name, family, context_window,
                supports_function_calling, supports_vision, max_output_tokens,
                created_at
            ) VALUES (
                '{model_id}',
                '{provider_id}',
                '{name}',
                '{family}',
                {context_window},
                {str(supports_function_calling).lower()},
                {str(supports_vision).lower()},
                {max_output_tokens},
                {created_at}
            )
        """)
    
    print(f"  Migrated {len(models)} llm models")
    
    # 4. Migrate agents
    print("\n4. Migrating agents...")
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
        auto_rag = bool(agent["auto_rag"])
        auto_rag_limit = agent["auto_rag_limit"] or 5
        is_default = bool(agent["is_default"])
        is_active = bool(agent["is_active"])
        created_at = int(datetime.fromisoformat(agent["created_at"]).timestamp() * 1000) if agent["created_at"] else int(time.time() * 1000)
        
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
    
    # 5. Migrate agent_workspace_mounts
    print("\n5. Migrating agent_workspace_mounts...")
    cursor.execute("SELECT id, agent_id, host_path, mount_name, container_path, readonly FROM agent_workspace_mounts")
    mounts = cursor.fetchall()
    
    for mount in mounts:
        mount_id = mount["id"]
        agent_id = mount["agent_id"]
        host_path = mount["host_path"]
        mount_name = mount["mount_name"]
        container_path = mount["container_path"] or f"/workspace/{mount_name}"
        readonly = bool(mount["readonly"])
        
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
    
    # 6. Migrate agent_channels
    print("\n6. Migrating agent_channels...")
    cursor.execute("SELECT id, agent_id, channel, sandbox_override, enabled, created_at FROM agent_channels")
    channels = cursor.fetchall()
    
    for channel in channels:
        channel_id = channel["id"]
        agent_id = channel["agent_id"]
        channel_name = channel["channel"]
        sandbox_override = channel["sandbox_override"] or ""
        enabled = bool(channel["enabled"])
        created_at = int(datetime.fromisoformat(channel["created_at"]).timestamp() * 1000) if channel["created_at"] else int(time.time() * 1000)
        
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
    
    # 7. Migrate settings
    print("\n7. Migrating settings...")
    cursor.execute("SELECT key, value, key_type, created_at, updated_at FROM settings")
    settings = cursor.fetchall()
    
    for setting in settings:
        key = setting["key"]
        value = setting["value"]
        key_type = setting["key_type"] or "api_key"
        created_at = int(datetime.fromisoformat(setting["created_at"]).timestamp() * 1000) if setting["created_at"] else int(time.time() * 1000)
        updated_at = int(datetime.fromisoformat(setting["updated_at"]).timestamp() * 1000) if setting["updated_at"] else created_at
        
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
    
    # 8. Migrate conversations
    print("\n8. Migrating conversations...")
    cursor.execute("""
        SELECT id, agent_id, channel, title, is_active, message_count, 
               rolling_summary, summary_covers_to, recent_tools_used,
               created_at, updated_at
        FROM conversations
    """)
    conversations = cursor.fetchall()
    
    for conv in conversations:
        conv_id = conv["id"]
        
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
    
    # 9. Migrate conversation_messages
    print("\n9. Migrating conversation_messages...")
    cursor.execute("""
        SELECT id, conversation_id, role, content, tool_calls, tool_call_id,
               token_count, status, created_at
        FROM conversation_messages
        ORDER BY created_at
    """)
    messages = cursor.fetchall()
    
    migrated_count = 0
    for msg in messages:
        msg_id = msg["id"]
        
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
    
    # 10. Migrate work_plans
    print("\n10. Migrating work_plans...")
    cursor.execute("""
        SELECT id, agent_id, conversation_id, parent_plan_id, title, status,
               created_at, updated_at, completed_at
        FROM work_plans
    """)
    work_plans = cursor.fetchall()
    
    for plan in work_plans:
        plan_id = plan["id"]
        
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
    
    # 11. Migrate work_items
    print("\n11. Migrating work_items...")
    cursor.execute("""
        SELECT id, plan_id, title, status, ordinal, context_snapshot,
               notes, files_changed, started_at, completed_at,
               created_at, updated_at
        FROM work_items
    """)
    work_items = cursor.fetchall()
    
    for item in work_items:
        item_id = item["id"]
        
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
    print(f"- Providers: {len(providers)} rows")
    print(f"- Provider API keys: {len(api_keys)} rows")
    print(f"- LLM models: {len(models)} rows")
    print(f"- Agents: {len(agents)} rows")
    print(f"- Agent workspace mounts: {len(mounts)} rows")
    print(f"- Agent channels: {len(channels)} rows")
    print(f"- Settings: {len(settings)} rows")
    print(f"- Conversations: {len(conversations)} rows")
    print(f"- Conversation messages: {len(messages)} rows")
    print(f"- Work plans: {len(work_plans)} rows")
    print(f"- Work items: {len(work_items)} rows")
    
    await stdb.close()
    sqlite_conn.close()

if __name__ == "__main__":
    asyncio.run(migrate_all())