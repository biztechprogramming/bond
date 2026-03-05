#!/usr/bin/env python3
"""Migrate providers, llm_models, and provider_api_keys tables."""

import asyncio
import sqlite3
import json
import time
from datetime import datetime
from backend.app.core.spacetimedb import StdbClient

def escape_sql(value):
    """Escape single quotes for SQL."""
    if value is None:
        return ''
    return str(value).replace("'", "''")

def make_optional_string(value):
    """Convert a string value to SpacetimeDB optional sum type.
    
    Returns:
        - {"some": value} if value is not None/empty
        - {"none": []} if value is None or empty string
    """
    if value:
        return {"some": value}
    else:
        return {"none": []}

async def migrate_providers_models():
    # Connect to SQLite
    sqlite_conn = sqlite3.connect('knowledge.db')
    sqlite_conn.row_factory = sqlite3.Row
    
    # Connect to SpacetimeDB
    stdb = StdbClient()
    
    print("=== Migrating providers, llm_models, and provider_api_keys ===\n")
    
    # Check current state
    print("1. Checking current state...")
    try:
        providers_count = await stdb.query("SELECT COUNT(*) as count FROM providers")
        models_count = await stdb.query("SELECT COUNT(*) as count FROM llm_models")
        keys_count = await stdb.query("SELECT COUNT(*) as count FROM provider_api_keys")
        
        print(f"   Current in SpacetimeDB:")
        print(f"     providers: {providers_count[0]['count'] if providers_count else 0}")
        print(f"     llm_models: {models_count[0]['count'] if models_count else 0}")
        print(f"     provider_api_keys: {keys_count[0]['count'] if keys_count else 0}")
    except Exception as e:
        print(f"   Error checking: {e}")
    
    # Migrate providers
    print("\n2. Migrating providers...")
    cursor = sqlite_conn.cursor()
    cursor.execute("""
        SELECT id, display_name, litellm_prefix, api_base_url, models_endpoint,
               models_fetch_method, auth_type, is_enabled, config,
               created_at, updated_at
        FROM providers
    """)
    rows = cursor.fetchall()
    
    print(f"   Found {len(rows)} providers in SQLite")
    
    migrated = 0
    failed = 0
    for row in rows:
        provider_id = row["id"]
        display_name = escape_sql(row["display_name"])
        litellm_prefix = escape_sql(row["litellm_prefix"])
        api_base_url = row["api_base_url"]  # Optional
        models_endpoint = row["models_endpoint"]  # Optional
        models_fetch_method = escape_sql(row["models_fetch_method"])
        auth_type = escape_sql(row["auth_type"])
        is_enabled = bool(row["is_enabled"])
        config = escape_sql(row["config"]) or "{}"
        created_at = int(datetime.fromisoformat(row["created_at"]).timestamp() * 1000) if row["created_at"] else int(time.time() * 1000)
        updated_at = int(datetime.fromisoformat(row["updated_at"]).timestamp() * 1000) if row["updated_at"] else created_at
        
        # Build arguments for add_provider reducer
        # Note: apiBaseUrl and modelsEndpoint are optional strings
        args = [
            provider_id,
            display_name,
            litellm_prefix,
            make_optional_string(api_base_url),  # Optional string
            make_optional_string(models_endpoint),  # Optional string
            models_fetch_method,
            auth_type,
            is_enabled,
            config,
            created_at,
            updated_at,
        ]
        
        # Try calling the reducer (snake_case based on previous pattern)
        success = await stdb.call_reducer("add_provider", args)
        if success:
            migrated += 1
            if migrated % 5 == 0:
                print(f"   Migrated {migrated} providers...")
        else:
            failed += 1
            if failed <= 3:
                print(f"   Failed to migrate provider {provider_id}")
    
    print(f"   Result: {migrated} migrated, {failed} failed")
    
    # Migrate llm_models
    print("\n3. Migrating llm_models...")
    cursor.execute("""
        SELECT id, provider_id, model_slug, display_name, context_window, is_available
        FROM llm_models
    """)
    rows = cursor.fetchall()
    
    print(f"   Found {len(rows)} llm_models in SQLite")
    
    migrated = 0
    failed = 0
    for row in rows:
        model_id = row["id"]
        provider = escape_sql(row["provider_id"])  # SQLite: provider_id, SpacetimeDB: provider
        model_id_val = escape_sql(row["model_slug"])  # SQLite: model_slug, SpacetimeDB: modelId
        display_name = escape_sql(row["display_name"])
        context_window = row["context_window"] or 0
        is_enabled = bool(row["is_available"])  # SQLite: is_available, SpacetimeDB: isEnabled
        
        # Build arguments for add_model reducer
        args = [
            model_id,
            provider,
            model_id_val,
            display_name,
            context_window,
            is_enabled,
        ]
        
        # Try calling the reducer
        success = await stdb.call_reducer("add_model", args)
        if success:
            migrated += 1
            if migrated % 10 == 0:
                print(f"   Migrated {migrated} models...")
        else:
            failed += 1
            if failed <= 3:
                print(f"   Failed to migrate model {model_id}")
    
    print(f"   Result: {migrated} migrated, {failed} failed")
    
    # Migrate provider_api_keys
    print("\n4. Migrating provider_api_keys...")
    cursor.execute("""
        SELECT provider_id, encrypted_value, key_type, created_at, updated_at
        FROM provider_api_keys
    """)
    rows = cursor.fetchall()
    
    print(f"   Found {len(rows)} provider_api_keys in SQLite")
    
    migrated = 0
    failed = 0
    for row in rows:
        provider_id = row["provider_id"]
        encrypted_value = escape_sql(row["encrypted_value"])
        key_type = escape_sql(row["key_type"])
        created_at = int(datetime.fromisoformat(row["created_at"]).timestamp() * 1000) if row["created_at"] else int(time.time() * 1000)
        updated_at = int(datetime.fromisoformat(row["updated_at"]).timestamp() * 1000) if row["updated_at"] else created_at
        
        # Build arguments for set_provider_api_key reducer
        args = [
            provider_id,
            encrypted_value,
            key_type,
            created_at,
            updated_at,
        ]
        
        # Try calling the reducer
        success = await stdb.call_reducer("set_provider_api_key", args)
        if success:
            migrated += 1
            print(f"   Migrated API key for provider {provider_id}")
        else:
            failed += 1
            print(f"   Failed to migrate API key for provider {provider_id}")
    
    print(f"   Result: {migrated} migrated, {failed} failed")
    
    print("\n=== Migration complete ===")
    
    # Verify migration
    print("\n5. Verifying migration...")
    try:
        providers_count = await stdb.query("SELECT COUNT(*) as count FROM providers")
        models_count = await stdb.query("SELECT COUNT(*) as count FROM llm_models")
        keys_count = await stdb.query("SELECT COUNT(*) as count FROM provider_api_keys")
        
        print(f"   Final counts in SpacetimeDB:")
        print(f"     providers: {providers_count[0]['count'] if providers_count else 0}")
        print(f"     llm_models: {models_count[0]['count'] if models_count else 0}")
        print(f"     provider_api_keys: {keys_count[0]['count'] if keys_count else 0}")
        
    except Exception as e:
        print(f"   Error verifying: {e}")
    
    await stdb.close()
    sqlite_conn.close()

if __name__ == "__main__":
    asyncio.run(migrate_providers_models())