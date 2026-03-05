#!/usr/bin/env python3
"""Migrate provider_api_keys table properly, updating dummy values."""

import asyncio
import sqlite3
from datetime import datetime
from backend.app.core.spacetimedb import StdbClient

async def migrate_provider_keys():
    # Connect to SQLite
    sqlite_conn = sqlite3.connect('knowledge.db')
    sqlite_conn.row_factory = sqlite3.Row
    
    # Connect to SpacetimeDB
    stdb = StdbClient()
    
    print("=== Migrating provider_api_keys ===")
    cursor = sqlite_conn.cursor()
    cursor.execute("SELECT provider_id, encrypted_value, key_type, created_at, updated_at FROM provider_api_keys")
    sqlite_keys = cursor.fetchall()
    
    print(f"Found {len(sqlite_keys)} provider API keys in SQLite:")
    for key in sqlite_keys:
        print(f"  - {key['provider_id']}: {key['encrypted_value'][:30]}...")
    
    # Check what's already in SpacetimeDB
    print("\nChecking SpacetimeDB provider_api_keys:")
    stdb_keys = await stdb.query("SELECT provider_id, encrypted_value FROM provider_api_keys")
    stdb_key_map = {row['provider_id']: row['encrypted_value'] for row in stdb_keys}
    
    for provider_id, value in stdb_key_map.items():
        print(f"  - {provider_id}: {value[:30]}...")
    
    # Migrate/update each key
    for sqlite_key in sqlite_keys:
        provider_id = sqlite_key['provider_id']
        sqlite_value = sqlite_key['encrypted_value']
        key_type = sqlite_key['key_type']
        
        # Check if key exists in SpacetimeDB
        if provider_id in stdb_key_map:
            stdb_value = stdb_key_map[provider_id]
            
            # Check if SpacetimeDB has a dummy value
            if 'dummy' in stdb_value.lower() and 'dummy' not in sqlite_value.lower():
                # SpacetimeDB has dummy, SQLite has real key - UPDATE
                print(f"\n  Updating {provider_id} (replacing dummy with real key)")
                
                # Convert timestamps
                created_at = sqlite_key['created_at']
                updated_at = sqlite_key['updated_at']
                
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
                
                # UPDATE the record
                sql = f"""
                UPDATE provider_api_keys 
                SET encrypted_value = '{sqlite_value.replace("'", "''")}',
                    key_type = '{key_type}',
                    created_at = {created_at_ms},
                    updated_at = {updated_at_ms}
                WHERE provider_id = '{provider_id}'
                """
                
                try:
                    await stdb.query(sql)
                    print(f"    Updated {provider_id}")
                except Exception as e:
                    print(f"    Failed to update {provider_id}: {e}")
            else:
                print(f"\n  Skipping {provider_id} (already exists with non-dummy value)")
        else:
            # Key doesn't exist in SpacetimeDB - INSERT
            print(f"\n  Inserting {provider_id} (new key)")
            
            # Convert timestamps
            created_at = sqlite_key['created_at']
            updated_at = sqlite_key['updated_at']
            
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
            
            # INSERT new record
            sql = f"""
            INSERT INTO provider_api_keys (provider_id, encrypted_value, key_type, created_at, updated_at)
            VALUES (
                '{provider_id}',
                '{sqlite_value.replace("'", "''")}',
                '{key_type}',
                {created_at_ms},
                {updated_at_ms}
            )
            """
            
            try:
                await stdb.query(sql)
                print(f"    Inserted {provider_id}")
            except Exception as e:
                print(f"    Failed to insert {provider_id}: {e}")
    
    sqlite_conn.close()
    await stdb.close()
    
    print("\n=== Migration complete ===")
    
    # Final check
    print("\nFinal state in SpacetimeDB:")
    stdb2 = StdbClient()
    rows = await stdb2.query("SELECT provider_id, encrypted_value FROM provider_api_keys")
    for row in rows:
        value_preview = row['encrypted_value']
        if len(value_preview) > 30:
            value_preview = value_preview[:30] + "..."
        print(f"  - {row['provider_id']}: {value_preview}")
    
    await stdb2.close()

if __name__ == "__main__":
    asyncio.run(migrate_provider_keys())