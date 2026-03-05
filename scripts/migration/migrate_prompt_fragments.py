#!/usr/bin/env python3
"""
Migrate prompt fragments data from SQLite to SpacetimeDB.
"""

import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path
import aiohttp
import ulid

# Configuration
SQLITE_DB_PATH = Path.home() / ".bond" / "data" / "knowledge.db"
SPACETIMEDB_URL = "http://localhost:18787"
DATABASE_NAME = "bond-core-v2"

async def migrate_prompt_fragments():
    """Migrate prompt_fragments table."""
    print("Migrating prompt_fragments...")
    
    # Connect to SQLite
    sqlite_conn = sqlite3.connect(SQLITE_DB_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    cursor = sqlite_conn.cursor()
    
    # Get all prompt fragments
    cursor.execute("SELECT * FROM prompt_fragments")
    rows = cursor.fetchall()
    
    async with aiohttp.ClientSession() as session:
        for row in rows:
            # Convert SQLite row to dict
            data = dict(row)
            
            # Convert timestamps to milliseconds
            created_at = int(datetime.fromisoformat(data['created_at']).timestamp() * 1000)
            updated_at = int(datetime.fromisoformat(data['updated_at']).timestamp() * 1000)
            
            # Convert boolean values
            is_active = bool(data['is_active'])
            is_system = bool(data['is_system'])
            
            # Prepare payload for SpacetimeDB reducer
            payload = {
                "id": data['id'],
                "name": data['name'],
                "display_name": data['display_name'],
                "category": data['category'],
                "content": data['content'],
                "description": data['description'] or '',
                "is_active": is_active,
                "is_system": is_system,
                "summary": data.get('summary', '') or '',
                "tier": data.get('tier', 'standard') or 'standard',
                "task_triggers": data.get('task_triggers', '[]') or '[]',
                "token_estimate": data.get('token_estimate', 0) or 0,
                "created_at": created_at,
                "updated_at": updated_at,
            }
            
            # Call SpacetimeDB reducer (note: SpacetimeDB converts camelCase to snake_case)
            try:
                async with session.post(
                    f"{SPACETIMEDB_URL}/v1/database/{DATABASE_NAME}/call/add_prompt_fragment",
                    json=payload
                ) as response:
                    if response.status == 200:
                        print(f"  Migrated fragment: {data['name']}")
                    else:
                        print(f"  Failed to migrate fragment {data['name']}: {response.status}")
            except Exception as e:
                print(f"  Error migrating fragment {data['name']}: {e}")
    
    sqlite_conn.close()
    print(f"Migrated {len(rows)} prompt fragments")

async def migrate_prompt_fragment_versions():
    """Migrate prompt_fragment_versions table."""
    print("\nMigrating prompt_fragment_versions...")
    
    sqlite_conn = sqlite3.connect(SQLITE_DB_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    cursor = sqlite_conn.cursor()
    
    cursor.execute("SELECT * FROM prompt_fragment_versions")
    rows = cursor.fetchall()
    
    async with aiohttp.ClientSession() as session:
        for row in rows:
            data = dict(row)
            
            # Convert timestamp
            created_at = int(datetime.fromisoformat(data['created_at']).timestamp() * 1000)
            
            payload = {
                "id": data['id'],
                "fragment_id": data['fragment_id'],
                "version": data['version'],
                "content": data['content'],
                "change_reason": data.get('change_reason', '') or '',
                "changed_by": data.get('changed_by', 'user') or 'user',
                "created_at": created_at,
            }
            
            try:
                async with session.post(
                    f"{SPACETIMEDB_URL}/v1/database/{DATABASE_NAME}/call/add_prompt_fragment_version",
                    json=payload
                ) as response:
                    if response.status == 200:
                        print(f"  Migrated version {data['version']} for fragment {data['fragment_id']}")
                    else:
                        print(f"  Failed to migrate version {data['version']}: {response.status}")
            except Exception as e:
                print(f"  Error migrating version {data['version']}: {e}")
    
    sqlite_conn.close()
    print(f"Migrated {len(rows)} prompt fragment versions")

async def migrate_agent_prompt_fragments():
    """Migrate agent_prompt_fragments table."""
    print("\nMigrating agent_prompt_fragments...")
    
    sqlite_conn = sqlite3.connect(SQLITE_DB_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    cursor = sqlite_conn.cursor()
    
    cursor.execute("SELECT * FROM agent_prompt_fragments")
    rows = cursor.fetchall()
    
    async with aiohttp.ClientSession() as session:
        for row in rows:
            data = dict(row)
            
            # Convert timestamp
            created_at = int(datetime.fromisoformat(data['created_at']).timestamp() * 1000)
            
            # Convert boolean
            enabled = bool(data['enabled'])
            
            payload = {
                "id": data['id'],
                "agent_id": data['agent_id'],
                "fragment_id": data['fragment_id'],
                "rank": data['rank'],
                "enabled": enabled,
                "created_at": created_at,
            }
            
            try:
                async with session.post(
                    f"{SPACETIMEDB_URL}/v1/database/{DATABASE_NAME}/call/add_agent_prompt_fragment",
                    json=payload
                ) as response:
                    if response.status == 200:
                        print(f"  Migrated attachment: agent={data['agent_id']}, fragment={data['fragment_id']}")
                    else:
                        print(f"  Failed to migrate attachment: {response.status}")
            except Exception as e:
                print(f"  Error migrating attachment: {e}")
    
    sqlite_conn.close()
    print(f"Migrated {len(rows)} agent prompt fragment attachments")

async def migrate_prompt_templates():
    """Migrate prompt_templates table."""
    print("\nMigrating prompt_templates...")
    
    sqlite_conn = sqlite3.connect(SQLITE_DB_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    cursor = sqlite_conn.cursor()
    
    cursor.execute("SELECT * FROM prompt_templates")
    rows = cursor.fetchall()
    
    async with aiohttp.ClientSession() as session:
        for row in rows:
            data = dict(row)
            
            # Convert timestamps
            created_at = int(datetime.fromisoformat(data['created_at']).timestamp() * 1000)
            updated_at = int(datetime.fromisoformat(data['updated_at']).timestamp() * 1000)
            
            # Convert boolean
            is_active = bool(data['is_active'])
            
            # Handle variables (might be JSON string or list)
            variables = data.get('variables', '[]')
            if isinstance(variables, str):
                try:
                    # Try to parse as JSON
                    json.loads(variables)
                except:
                    # If it's not valid JSON, wrap it as a JSON array
                    variables = '[]'
            else:
                # Convert to JSON string
                variables = json.dumps(variables)
            
            payload = {
                "id": data['id'],
                "name": data['name'],
                "display_name": data['display_name'],
                "category": data['category'],
                "content": data['content'],
                "variables": variables,
                "description": data.get('description', '') or '',
                "is_active": is_active,
                "created_at": created_at,
                "updated_at": updated_at,
            }
            
            try:
                async with session.post(
                    f"{SPACETIMEDB_URL}/v1/database/{DATABASE_NAME}/call/add_prompt_template",
                    json=payload
                ) as response:
                    if response.status == 200:
                        print(f"  Migrated template: {data['name']}")
                    else:
                        print(f"  Failed to migrate template {data['name']}: {response.status}")
            except Exception as e:
                print(f"  Error migrating template {data['name']}: {e}")
    
    sqlite_conn.close()
    print(f"Migrated {len(rows)} prompt templates")

async def migrate_prompt_template_versions():
    """Migrate prompt_template_versions table."""
    print("\nMigrating prompt_template_versions...")
    
    sqlite_conn = sqlite3.connect(SQLITE_DB_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    cursor = sqlite_conn.cursor()
    
    cursor.execute("SELECT * FROM prompt_template_versions")
    rows = cursor.fetchall()
    
    async with aiohttp.ClientSession() as session:
        for row in rows:
            data = dict(row)
            
            # Convert timestamp
            created_at = int(datetime.fromisoformat(data['created_at']).timestamp() * 1000)
            
            payload = {
                "id": data['id'],
                "template_id": data['template_id'],
                "version": data['version'],
                "content": data['content'],
                "change_reason": data.get('change_reason', '') or '',
                "changed_by": data.get('changed_by', 'user') or 'user',
                "created_at": created_at,
            }
            
            try:
                async with session.post(
                    f"{SPACETIMEDB_URL}/v1/database/{DATABASE_NAME}/call/add_prompt_template_version",
                    json=payload
                ) as response:
                    if response.status == 200:
                        print(f"  Migrated template version {data['version']} for template {data['template_id']}")
                    else:
                        print(f"  Failed to migrate template version {data['version']}: {response.status}")
            except Exception as e:
                print(f"  Error migrating template version {data['version']}: {e}")
    
    sqlite_conn.close()
    print(f"Migrated {len(rows)} prompt template versions")

async def main():
    """Main migration function."""
    print("Starting migration of prompt fragments data to SpacetimeDB...")
    
    # Check if SQLite database exists
    if not SQLITE_DB_PATH.exists():
        print(f"SQLite database not found at {SQLITE_DB_PATH}")
        return
    
    # Run migrations
    await migrate_prompt_fragments()
    await migrate_prompt_fragment_versions()
    await migrate_agent_prompt_fragments()
    await migrate_prompt_templates()
    await migrate_prompt_template_versions()
    
    print("\nMigration completed!")

if __name__ == "__main__":
    asyncio.run(main())