#!/usr/bin/env python3
"""Check what's in SpacetimeDB tables."""

import json
import urllib.request
import urllib.error

SPACETIMEDB_URL = "http://localhost:18787"
MODULE_NAME = "bond-core-v2"

def query_sql(sql: str):
    """Execute SQL query against SpacetimeDB."""
    url = f"{SPACETIMEDB_URL}/v1/database/{MODULE_NAME}/sql"
    req = urllib.request.Request(url, data=sql.encode(), method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        print(f"Error: {e.code} {e.read().decode()[:200]}")
        return None

def main():
    print("Checking SpacetimeDB tables...")
    
    # Check what tables exist
    print("\n1. Checking system tables...")
    result = query_sql("SELECT * FROM system_table")
    if result:
        tables = []
        for table in result[0]["rows"]:
            tables.append(table[0])  # table_name is first column
        print(f"Tables found: {tables}")
    
    # Check settings table
    print("\n2. Checking settings table...")
    result = query_sql("SELECT * FROM settings")
    if result and result[0]["rows"]:
        print(f"Settings found: {len(result[0]['rows'])} rows")
        for row in result[0]["rows"]:
            print(f"  - {row[0]}: {row[1][:50]}...")
    else:
        print("No settings found or table doesn't exist")
    
    # Check provider_api_keys table
    print("\n3. Checking provider_api_keys table...")
    result = query_sql("SELECT * FROM provider_api_keys")
    if result and result[0]["rows"]:
        print(f"Provider API keys found: {len(result[0]['rows'])} rows")
        for row in result[0]["rows"]:
            print(f"  - {row[0]}: {row[1][:50]}...")
    else:
        print("No provider API keys found or table doesn't exist")
    
    # Check if there's a bond-core module
    print("\n4. Checking available modules...")
    try:
        req = urllib.request.Request(f"{SPACETIMEDB_URL}/v1/database")
        with urllib.request.urlopen(req) as resp:
            modules = json.load(resp)
            print(f"Modules: {modules}")
    except Exception as e:
        print(f"Error checking modules: {e}")

if __name__ == "__main__":
    main()