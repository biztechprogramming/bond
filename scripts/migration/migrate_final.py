#!/usr/bin/env python3
"""Final migration with proper escaping."""

import asyncio
import json
import sqlite3
import time
from datetime import datetime
from backend.app.core.spacetimedb import StdbClient

def escape_sql(value):
    """Escape single quotes for SQL."""
    if value is None:
        return ''
    return str(value).replace("'", "''")

async def migrate_final():
    # Connect to SQLite
    sqlite_conn = sqlite3.connect('knowledge.db')
    sqlite_conn.row_factory = sqlite3.Row
    
    # Connect to SpacetimeDB
    stdb = StdbClient()
    
    print("=== Final migration with proper escaping ===")
    
    # Migrate in this order to respect foreign keys
    tables = [
        ('agents', 'agents'),
        ('agent_workspace_mounts', 'agent_workspace_mounts'),
        ('agent_channels', 'agent_channels'),
        ('settings', 'settings'),
        ('conversations', 'conversations'),
        ('conversation_messages', 'conversation_messages'),
        ('work_plans', 'work_plans'),
        ('work_items', 'work_items'),
    ]
    
    for sqlite_table, stdb_table in tables:
        print(f"\nMigrating {sqlite_table}...")
        
        if sqlite_table == 'agents':
            cursor = sqlite_conn.cursor()
            cursor.execute("""
                SELECT id, name, display_name, system_prompt, model, utility_model, 
                       tools, sandbox_image, max_iterations, auto_rag, auto_rag_limit,
                       is_default, is_active, created_at
                FROM agents
            """)
            rows = cursor.fetchall()
            
            for row in rows:
                agent_id = row["id"]
                name = escape_sql(row["name"])
                display_name = escape_sql(row["display_name"])
                system_prompt = escape_sql(row["system_prompt"])
                model = escape_sql(row["model"])
                utility_model = escape_sql(row["utility_model"]) or "claude-sonnet-4-6"
                tools = escape_sql(row["tools"]) or "[]"
                sandbox_image = escape_sql(row["sandbox_image"])
                max_iterations = row["max_iterations"] or 25
                is_default = bool(row["is_default"])
                is_active = bool(row["is_active"])
                created_at = int(datetime.fromisoformat(row["created_at"]).timestamp() * 1000) if row["created_at"] else int(time.time() * 1000)
                
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
            print(f"  Migrated {len(rows)} agents")
            
        elif sqlite_table == 'agent_workspace_mounts':
            cursor = sqlite_conn.cursor()
            cursor.execute("SELECT id, agent_id, host_path, mount_name, container_path, readonly FROM agent_workspace_mounts")
            rows = cursor.fetchall()
            
            for row in rows:
                mount_id = row["id"]
                agent_id = row["agent_id"]
                host_path = escape_sql(row["host_path"])
                mount_name = escape_sql(row["mount_name"])
                container_path = escape_sql(row["container_path"]) or f"/workspace/{mount_name}"
                readonly = bool(row["readonly"])
                
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
            print(f"  Migrated {len(rows)} mounts")
            
        elif sqlite_table == 'agent_channels':
            cursor = sqlite_conn.cursor()
            cursor.execute("SELECT id, agent_id, channel, sandbox_override, enabled, created_at FROM agent_channels")
            rows = cursor.fetchall()
            
            for row in rows:
                channel_id = row["id"]
                agent_id = row["agent_id"]
                channel_name = escape_sql(row["channel"])
                sandbox_override = escape_sql(row["sandbox_override"])
                enabled = bool(row["enabled"])
                created_at = int(datetime.fromisoformat(row["created_at"]).timestamp() * 1000) if row["created_at"] else int(time.time() * 1000)
                
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
            print(f"  Migrated {len(rows)} channels")
            
        elif sqlite_table == 'settings':
            cursor = sqlite_conn.cursor()
            cursor.execute("SELECT key, value, key_type, created_at, updated_at FROM settings")
            rows = cursor.fetchall()
            
            for row in rows:
                key = escape_sql(row["key"])
                value = escape_sql(row["value"])
                key_type = escape_sql(row["key_type"]) or "api_key"
                created_at = int(datetime.fromisoformat(row["created_at"]).timestamp() * 1000) if row["created_at"] else int(time.time() * 1000)
                updated_at = int(datetime.fromisoformat(row["updated_at"]).timestamp() * 1000) if row["updated_at"] else created_at
                
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
            print(f"  Migrated {len(rows)} settings")
            
        elif sqlite_table == 'conversations':
            cursor = sqlite_conn.cursor()
            cursor.execute("""
                SELECT id, agent_id, channel, title, is_active, message_count, 
                       rolling_summary, summary_covers_to, recent_tools_used,
                       created_at, updated_at
                FROM conversations
            """)
            rows = cursor.fetchall()
            
            for row in rows:
                conv_id = row["id"]
                agent_id = row["agent_id"]
                channel = escape_sql(row["channel"])
                title = escape_sql(row["title"])
                is_active = bool(row["is_active"])
                message_count = row["message_count"] or 0
                rolling_summary = escape_sql(row["rolling_summary"])
                summary_coversto = row["summary_covers_to"] or 0
                recent_tools_used = escape_sql(row["recent_tools_used"]) or "[]"
                created_at = int(datetime.fromisoformat(row["created_at"]).timestamp() * 1000) if row["created_at"] else int(time.time() * 1000)
                updated_at = int(datetime.fromisoformat(row["updated_at"]).timestamp() * 1000) if row["updated_at"] else created_at
                
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
            print(f"  Migrated {len(rows)} conversations")
            
        elif sqlite_table == 'conversation_messages':
            cursor = sqlite_conn.cursor()
            cursor.execute("""
                SELECT id, conversation_id, role, content, tool_calls, tool_call_id,
                       token_count, status, created_at
                FROM conversation_messages
                ORDER BY created_at
                LIMIT 1000
            """)
            rows = cursor.fetchall()
            
            migrated = 0
            for row in rows:
                msg_id = row["id"]
                conversation_id = row["conversation_id"]
                role = escape_sql(row["role"])
                content = escape_sql(row["content"])
                tool_calls = escape_sql(row["tool_calls"])
                tool_call_id = escape_sql(row["tool_call_id"])
                token_count = row["token_count"] or 0
                status = escape_sql(row["status"]) or "delivered"
                created_at = int(datetime.fromisoformat(row["created_at"]).timestamp() * 1000) if row["created_at"] else int(time.time() * 1000)
                
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
                migrated += 1
                if migrated % 100 == 0:
                    print(f"  Migrated {migrated} messages...")
            print(f"  Migrated {migrated} conversation messages total")
            
        elif sqlite_table == 'work_plans':
            cursor = sqlite_conn.cursor()
            cursor.execute("""
                SELECT id, agent_id, conversation_id, parent_plan_id, title, status,
                       created_at, updated_at, completed_at
                FROM work_plans
            """)
            rows = cursor.fetchall()
            
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
                # SpacetimeDB doesn't support NULL for optional columns in SQL
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
            print(f"  Migrated {len(rows)} work plans")
            
        elif sqlite_table == 'work_items':
            cursor = sqlite_conn.cursor()
            cursor.execute("""
                SELECT id, plan_id, title, status, ordinal, context_snapshot,
                       notes, files_changed, started_at, completed_at,
                       created_at, updated_at
                FROM work_items
            """)
            rows = cursor.fetchall()
            
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
                
                # For optional columns, include only if > 0
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
            print(f"  Migrated {len(rows)} work items")
    
    print("\n=== Migration complete ===")
    
    await stdb.close()
    sqlite_conn.close()

if __name__ == "__main__":
    asyncio.run(migrate_final())