#!/usr/bin/env python3
"""Migrate settings and API keys from SQLite to SpacetimeDB."""

import asyncio
import json
import sqlite3
from datetime import datetime
from backend.app.core.spacetimedb import StdbClient

async def migrate_settings():
    # Connect to SQLite
    sqlite_conn = sqlite3.connect('knowledge.db')
    sqlite_conn.row_factory = sqlite3.Row
    
    # Connect to SpacetimeDB
    stdb = StdbClient()
    
    print("=== Migrating settings table ===")
    cursor = sqlite_conn.cursor()
    cursor.execute("SELECT key, value, key_type, created_at, updated_at FROM settings")
    settings = cursor.fetchall()
    print(f"Found {len(settings)} settings in SQLite")
    
    for setting in settings:
        key = setting['key']
        value = setting['value']
        key_type = setting['key_type']
        
        # Check if setting already exists in SpacetimeDB
        existing = await stdb.query(f"SELECT key FROM settings WHERE key = '{key}'")
        if existing:
            print(f"  Setting '{key}' already exists in SpacetimeDB, skipping")
            continue
        
        # Convert timestamps
        created_at = setting['created_at']
        updated_at = setting['updated_at']
        
        def convert_timestamp(ts):
            if isinstance(ts, str):
                try:
                    dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                    return int(dt.timestamp() * 1000)
                except:
                    return int(datetime.now().timestamp() * 1000)
            else:
                return int(datetime.now().timestamp() * 1000)
        
        created_at_ms = convert_timestamp(created_at)
        updated_at_ms = convert_timestamp(updated_at)
        
        # Insert into SpacetimeDB
        sql = f"""
        INSERT INTO settings (key, value, key_type, created_at, updated_at)
        VALUES (
            '{key.replace("'", "''")}',
            '{value.replace("'", "''")}',
            '{key_type}',
            {created_at_ms},
            {updated_at_ms}
        )
        """
        
        try:
            await stdb.query(sql)
            print(f"  Migrated setting: {key}")
        except Exception as e:
            print(f"  Failed to migrate setting {key}: {e}")
    
    print("\n=== Migrating provider_api_keys table ===")
    cursor.execute("SELECT provider_id, encrypted_value, key_type, created_at, updated_at FROM provider_api_keys")
    api_keys = cursor.fetchall()
    print(f"Found {len(api_keys)} provider API keys in SQLite")
    
    for key in api_keys:
        provider_id = key['provider_id']
        encrypted_value = key['encrypted_value']
        key_type = key['key_type']
        
        # Check if key already exists in SpacetimeDB
        existing = await stdb.query(f"SELECT provider_id FROM provider_api_keys WHERE provider_id = '{provider_id}'")
        if existing:
            print(f"  API key for '{provider_id}' already exists in SpacetimeDB, skipping")
            continue
        
        # Convert timestamps
        created_at = key['created_at']
        updated_at = key['updated_at']
        created_at_ms = convert_timestamp(created_at)
        updated_at_ms = convert_timestamp(updated_at)
        
        # Insert into SpacetimeDB
        sql = f"""
        INSERT INTO provider_api_keys (provider_id, encrypted_value, key_type, created_at, updated_at)
        VALUES (
            '{provider_id}',
            '{encrypted_value.replace("'", "''")}',
            '{key_type}',
            {created_at_ms},
            {updated_at_ms}
        )
        """
        
        try:
            await stdb.query(sql)
            print(f"  Migrated API key for: {provider_id}")
        except Exception as e:
            print(f"  Failed to migrate API key for {provider_id}: {e}")
    
    print("\n=== Migrating providers table ===")
    cursor.execute("SELECT id, display_name, litellm_prefix, api_base_url, models_endpoint, models_fetch_method, auth_type, is_enabled, config, created_at, updated_at FROM providers")
    providers = cursor.fetchall()
    print(f"Found {len(providers)} providers in SQLite")
    
    for provider in providers:
        provider_id = provider['id']
        
        # Check if provider already exists in SpacetimeDB
        existing = await stdb.query(f"SELECT id FROM providers WHERE id = '{provider_id}'")
        if existing:
            print(f"  Provider '{provider_id}' already exists in SpacetimeDB, skipping")
            continue
        
        # Convert timestamps
        created_at = provider['created_at']
        updated_at = provider['updated_at']
        created_at_ms = convert_timestamp(created_at)
        updated_at_ms = convert_timestamp(updated_at)
        
        # Handle config JSON
        config = provider['config']
        if config and not isinstance(config, str):
            config = json.dumps(config)
        
        # Insert into SpacetimeDB
        sql = f"""
        INSERT INTO providers (
            id, display_name, litellm_prefix, api_base_url, models_endpoint, 
            models_fetch_method, auth_type, is_enabled, config, created_at, updated_at
        ) VALUES (
            '{provider_id}',
            '{provider['display_name'].replace("'", "''")}',
            '{provider['litellm_prefix']}',
            '{provider['api_base_url'] or ''}',
            '{provider['models_endpoint'] or ''}',
            '{provider['models_fetch_method'] or ''}',
            '{provider['auth_type'] or ''}',
            {'true' if provider['is_enabled'] else 'false'},
            '{config.replace("'", "''") if config else ''}',
            {created_at_ms},
            {updated_at_ms}
        )
        """
        
        try:
            await stdb.query(sql)
            print(f"  Migrated provider: {provider_id}")
        except Exception as e:
            print(f"  Failed to migrate provider {provider_id}: {e}")
    
    sqlite_conn.close()
    await stdb.close()
    print("\n=== Migration complete! ===")
    
    # Final check
    print("\nFinal check in SpacetimeDB:")
    stdb2 = StdbClient()
    
    settings_count = len(await stdb2.query("SELECT key FROM settings"))
    print(f"  Settings: {settings_count} rows")
    
    keys_count = len(await stdb2.query("SELECT provider_id FROM provider_api_keys"))
    print(f"  Provider API keys: {keys_count} rows")
    
    providers_count = len(await stdb2.query("SELECT id FROM providers"))
    print(f"  Providers: {providers_count} rows")
    
    await stdb2.close()

if __name__ == "__main__":
    asyncio.run(migrate_settings())