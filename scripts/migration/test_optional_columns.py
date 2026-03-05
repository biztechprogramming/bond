#!/usr/bin/env python3
"""Test different syntaxes for optional columns in SpacetimeDB SQL."""

import asyncio
import json
from backend.app.core.spacetimedb import StdbClient

async def test_optional_syntax():
    stdb = StdbClient()
    
    print("=== Testing Optional Column Syntax in SpacetimeDB ===\n")
    
    # First, let's check if we can query the schema
    print("1. Checking work_plans schema...")
    try:
        result = await stdb.query("SELECT * FROM work_plans LIMIT 0")
        print(f"   Schema query succeeded (empty result expected)")
    except Exception as e:
        print(f"   Error: {e}")
    
    # Test different syntaxes
    test_cases = [
        # (sql, description, expected_to_work)
        ("INSERT INTO work_plans (id, agent_id, conversation_id, parent_plan_id, title, status, created_at, updated_at, completed_at) VALUES ('test-opt-1', 'test-agent', 'test-conv', '', 'Test', 'active', 1000, 1000, 1000)", "Plain number", False),
        ("INSERT INTO work_plans (id, agent_id, conversation_id, parent_plan_id, title, status, created_at, updated_at, completed_at) VALUES ('test-opt-2', 'test-agent', 'test-conv', '', 'Test', 'active', 1000, 1000, NULL)", "NULL", False),
        ("INSERT INTO work_plans (id, agent_id, conversation_id, parent_plan_id, title, status, created_at, updated_at) VALUES ('test-opt-3', 'test-agent', 'test-conv', '', 'Test', 'active', 1000, 1000)", "Omit optional column", False),
        ("INSERT INTO work_plans (id, agent_id, conversation_id, parent_plan_id, title, status, created_at, updated_at, completed_at) VALUES ('test-opt-4', 'test-agent', 'test-conv', '', 'Test', 'active', 1000, 1000, 'none')", "String 'none'", False),
        ("INSERT INTO work_plans (id, agent_id, conversation_id, parent_plan_id, title, status, created_at, updated_at, completed_at) VALUES ('test-opt-5', 'test-agent', 'test-conv', '', 'Test', 'active', 1000, 1000, 'NONE')", "String 'NONE'", False),
        ("INSERT INTO work_plans (id, agent_id, conversation_id, parent_plan_id, title, status, created_at, updated_at, completed_at) VALUES ('test-opt-6', 'test-agent', 'test-conv', '', 'Test', 'active', 1000, 1000, '()')", "Empty tuple '()'", False),
        ("INSERT INTO work_plans (id, agent_id, conversation_id, parent_plan_id, title, status, created_at, updated_at, completed_at) VALUES ('test-opt-7', 'test-agent', 'test-conv', '', 'Test', 'active', 1000, 1000, 'none()')", "none()", False),
        ("INSERT INTO work_plans (id, agent_id, conversation_id, parent_plan_id, title, status, created_at, updated_at, completed_at) VALUES ('test-opt-8', 'test-agent', 'test-conv', '', 'Test', 'active', 1000, 1000, 'NONE()')", "NONE()", False),
        ("INSERT INTO work_plans (id, agent_id, conversation_id, parent_plan_id, title, status, created_at, updated_at, completed_at) VALUES ('test-opt-9', 'test-agent', 'test-conv', '', 'Test', 'active', 1000, 1000, '(none)')", "(none)", False),
        ("INSERT INTO work_plans (id, agent_id, conversation_id, parent_plan_id, title, status, created_at, updated_at, completed_at) VALUES ('test-opt-10', 'test-agent', 'test-conv', '', 'Test', 'active', 1000, 1000, '(NONE)')", "(NONE)", False),
        ("INSERT INTO work_plans (id, agent_id, conversation_id, parent_plan_id, title, status, created_at, updated_at, completed_at) VALUES ('test-opt-11', 'test-agent', 'test-conv', '', 'Test', 'active', 1000, 1000, '(none: ())')", "(none: ())", True),  # From error message
        ("INSERT INTO work_plans (id, agent_id, conversation_id, parent_plan_id, title, status, created_at, updated_at, completed_at) VALUES ('test-opt-12', 'test-agent', 'test-conv', '', 'Test', 'active', 1000, 1000, '(some: 1000)')", "(some: 1000)", True),  # From error message
        ("INSERT INTO work_plans (id, agent_id, conversation_id, parent_plan_id, title, status, created_at, updated_at, completed_at) VALUES ('test-opt-13', 'test-agent', 'test-conv', '', 'Test', 'active', 1000, 1000, 'some(1000)')", "some(1000)", False),
        ("INSERT INTO work_plans (id, agent_id, conversation_id, parent_plan_id, title, status, created_at, updated_at, completed_at) VALUES ('test-opt-14', 'test-agent', 'test-conv', '', 'Test', 'active', 1000, 1000, 'SOME(1000)')", "SOME(1000)", False),
        ("INSERT INTO work_plans (id, agent_id, conversation_id, parent_plan_id, title, status, created_at, updated_at, completed_at) VALUES ('test-opt-15', 'test-agent', 'test-conv', '', 'Test', 'active', 1000, 1000, '\\\"some\\\": 1000')", '{"some": 1000} as string', False),
        ("INSERT INTO work_plans (id, agent_id, conversation_id, parent_plan_id, title, status, created_at, updated_at, completed_at) VALUES ('test-opt-16', 'test-agent', 'test-conv', '', 'Test', 'active', 1000, 1000, '{\\\"some\\\": 1000}')", '{"some": 1000} JSON', False),
        ("INSERT INTO work_plans (id, agent_id, conversation_id, parent_plan_id, title, status, created_at, updated_at, completed_at) VALUES ('test-opt-17', 'test-agent', 'test-conv', '', 'Test', 'active', 1000, 1000, '{\\\"Some\\\": 1000}')", '{"Some": 1000} JSON', False),
    ]
    
    # Clean up any previous test data
    print("\n2. Cleaning up previous test data...")
    try:
        await stdb.query("DELETE FROM work_plans WHERE id LIKE 'test-opt-%'")
        print("   Cleanup done")
    except Exception as e:
        print(f"   Cleanup error (may be expected): {e}")
    
    print("\n3. Testing INSERT syntaxes:")
    successful = []
    failed = []
    
    for sql, description, expected in test_cases:
        print(f"\n   Testing: {description}")
        print(f"   SQL: {sql[:100]}...")
        try:
            result = await stdb.query(sql)
            print(f"   ✓ Success!")
            successful.append((description, sql))
        except Exception as e:
            error_str = str(e)
            # Truncate long errors
            if len(error_str) > 150:
                error_str = error_str[:150] + "..."
            print(f"   ✗ Error: {error_str}")
            failed.append((description, sql, error_str))
    
    print(f"\n=== Results: {len(successful)} succeeded, {len(failed)} failed ===")
    
    if successful:
        print("\nSuccessful syntaxes:")
        for desc, sql in successful:
            print(f"  - {desc}")
            print(f"    SQL: {sql}")
    
    if failed:
        print("\nFailed syntaxes (first few):")
        for desc, sql, error in failed[:5]:
            print(f"  - {desc}")
            print(f"    Error: {error}")
    
    # Check if any data was actually inserted
    print("\n4. Checking if any test data was inserted...")
    try:
        result = await stdb.query("SELECT COUNT(*) as count FROM work_plans WHERE id LIKE 'test-opt-%'")
        count = result[0]["count"] if result else 0
        print(f"   Found {count} test rows")
        if count > 0:
            rows = await stdb.query("SELECT id, completed_at FROM work_plans WHERE id LIKE 'test-opt-%' LIMIT 3")
            for row in rows:
                print(f"   - {row['id']}: completed_at = {row.get('completed_at', 'NOT FOUND')}")
    except Exception as e:
        print(f"   Error checking test data: {e}")
    
    await stdb.close()

if __name__ == "__main__":
    asyncio.run(test_optional_syntax())