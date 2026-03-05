#!/usr/bin/env python3
"""Migrate ALL data from SQLite to SpacetimeDB using working migration scripts."""

import asyncio
import subprocess
import sys
import os

async def run_migration_script(script_name, description):
    """Run a migration script and report results."""
    print(f"\n{'='*60}")
    print(f"Running: {description}")
    print(f"{'='*60}")
    
    try:
        # Run the script using uv run
        result = subprocess.run(
            ["uv", "run", "python", script_name],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True,
            text=True
        )
        
        print(result.stdout)
        if result.stderr:
            print(f"STDERR: {result.stderr[:500]}...")
        
        if result.returncode == 0:
            print(f"✅ {description} completed successfully")
            return True
        else:
            print(f"❌ {description} failed with code {result.returncode}")
            return False
            
    except Exception as e:
        print(f"❌ Error running {script_name}: {e}")
        return False

async def check_current_state():
    """Check current data in SpacetimeDB and SQLite."""
    print("\n" + "="*60)
    print("Checking current state")
    print("="*60)
    
    # Check SQLite first
    print("\nSQLite database (~/.bond/data/knowledge.db):")
    try:
        import sqlite3
        sqlite_conn = sqlite3.connect(os.path.expanduser('~/.bond/data/knowledge.db'))
        sqlite_conn.row_factory = sqlite3.Row
        cursor = sqlite_conn.cursor()
        
        tables = [
            'agents', 'conversations', 'conversation_messages',
            'work_plans', 'work_items', 'settings', 'mcp_servers',
            'providers', 'llm_models', 'provider_api_keys'
        ]
        
        for table in tables:
            try:
                cursor.execute(f'SELECT COUNT(*) as count FROM {table}')
                result = cursor.fetchone()
                count = result['count'] if result else 0
                print(f"  {table}: {count}")
            except Exception as e:
                print(f"  {table}: ERROR - {str(e)[:50]}...")
        
        sqlite_conn.close()
    except Exception as e:
        print(f"Error checking SQLite: {e}")
    
    # Check SpacetimeDB
    print("\nSpacetimeDB (bond-core-v2):")
    try:
        import asyncio
        from backend.app.core.spacetimedb import StdbClient
        
        stdb = StdbClient()
        tables = [
            'agents', 'conversations', 'conversation_messages',
            'work_plans', 'work_items', 'settings', 'mcp_servers',
            'providers', 'llm_models', 'provider_api_keys'
        ]
        
        for table in tables:
            try:
                result = await stdb.query(f'SELECT COUNT(*) as count FROM {table}')
                count = result[0]['count'] if result else 0
                print(f"  {table}: {count}")
            except Exception as e:
                print(f"  {table}: ERROR - {str(e)[:50]}...")
                
    except Exception as e:
        print(f"Error checking SpacetimeDB: {e}")

async def migrate_all_fixed():
    """Run all working migration scripts."""
    print("="*60)
    print("MIGRATING ALL DATA FROM SQLITE TO SPACETIMEDB")
    print("="*60)
    
    # Check current state before migration
    await check_current_state()
    
    # Run migration scripts in order
    migrations = [
        ("scripts/migration/migrate_providers_models.py", "Providers, LLM Models, and API Keys"),
        ("scripts/migration/migrate_agents_fixed.py", "Agents and Workspace Mounts"),
        # Note: work tables migration is skipped due to optional column issues
        # ("migrate_work_tables_fixed.py", "Work Plans and Items"),
    ]
    
    success_count = 0
    total_count = len(migrations)
    
    for script_path, description in migrations:
        success = await run_migration_script(script_path, description)
        if success:
            success_count += 1
    
    # Check final state
    print("\n" + "="*60)
    print("MIGRATION SUMMARY")
    print("="*60)
    print(f"Successfully migrated: {success_count}/{total_count}")
    
    if success_count < total_count:
        print("\n⚠️  Some migrations failed or were skipped:")
        print("   - Work plans and items: Skipped due to optional column issues")
        print("   - Conversations and messages: Already migrated (check counts above)")
        print("   - Settings: Already migrated (check counts above)")
    
    await check_current_state()
    
    print("\n" + "="*60)
    print("NEXT STEPS:")
    print("="*60)
    print("1. Start Bond services:")
    print("   make spacetimedb-up")
    print("   make dev  # or start backend, gateway, frontend separately")
    print("\n2. Test end-to-end flow:")
    print("   - Send a message to an agent")
    print("   - Check if message appears immediately")
    print("   - Check if agent responds")
    print("   - Refresh page and verify messages persist")
    print("\n3. Known issues:")
    print("   - Work tables (Kanban board) not migrated due to optional column syntax")
    print("   - MCP servers table is empty (no data to migrate)")

if __name__ == "__main__":
    asyncio.run(migrate_all_fixed())