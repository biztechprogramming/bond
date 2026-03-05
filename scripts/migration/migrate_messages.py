#!/usr/bin/env python3
"""Migrate conversation_messages to messages table."""

import asyncio
import json
from backend.app.core.spacetimedb import StdbClient

async def migrate_messages():
    stdb = StdbClient()
    
    print("=== Migrating conversation_messages to messages table ===")
    
    # First, check if messages table exists and has data
    try:
        result = await stdb.query("SELECT COUNT(*) as count FROM messages")
        print(f"messages table currently has: {result[0]['count']} rows")
    except Exception as e:
        print(f"Error checking messages table: {e}")
        print("messages table might not exist or have different schema")
        return
    
    # Get all conversation_messages
    try:
        rows = await stdb.query("""
            SELECT id, conversationId as sessionId, role, content, 
                   createdAt, toolCalls, toolCallId, tokenCount, status
            FROM conversationMessages
        """)
        print(f"Found {len(rows)} conversation_messages to migrate")
    except Exception as e:
        print(f"Error querying conversationMessages: {e}")
        return
    
    # Migrate each message
    migrated = 0
    errors = 0
    
    for row in rows:
        msg_id = row["id"]
        session_id = row["sessionId"]
        role = row["role"]
        content = row["content"]
        created_at = row["createdAt"]
        
        # Create metadata JSON
        metadata = {
            "source": "migrated_from_conversation_messages",
            "original_created_at": created_at
        }
        
        # Check if message already exists
        try:
            existing = await stdb.query(f"SELECT id FROM messages WHERE id = '{msg_id}'")
            if existing:
                print(f"  Message {msg_id} already exists in messages table, skipping")
                continue
        except:
            pass
        
        # Insert into messages table
        try:
            # Use save_message reducer if it exists
            await stdb.call_reducer("save_message", [
                msg_id,
                "",  # agentId - we don't have this in conversation_messages
                session_id,
                role,
                content,
                json.dumps(metadata)
            ])
            migrated += 1
            if migrated % 100 == 0:
                print(f"  Migrated {migrated} messages...")
        except Exception as e:
            # Try direct SQL insert as fallback
            try:
                # Escape single quotes
                escaped_content = content.replace("'", "''")
                escaped_metadata = json.dumps(metadata).replace("'", "''")
                
                await stdb.query(f"""
                    INSERT INTO messages (
                        id, agentId, sessionId, role, content, metadata, createdAt
                    ) VALUES (
                        '{msg_id}',
                        '',
                        '{session_id}',
                        '{role}',
                        '{escaped_content}',
                        '{escaped_metadata}',
                        {created_at}
                    )
                """)
                migrated += 1
                if migrated % 100 == 0:
                    print(f"  Migrated {migrated} messages...")
            except Exception as e2:
                print(f"  Failed to migrate message {msg_id}: {e2}")
                errors += 1
    
    print(f"\n=== Migration complete ===")
    print(f"Successfully migrated: {migrated} messages")
    print(f"Errors: {errors}")
    
    # Verify counts
    try:
        result = await stdb.query("SELECT COUNT(*) as count FROM messages")
        print(f"messages table now has: {result[0]['count']} rows")
    except Exception as e:
        print(f"Error checking final count: {e}")
    
    await stdb.close()

if __name__ == "__main__":
    asyncio.run(migrate_messages())