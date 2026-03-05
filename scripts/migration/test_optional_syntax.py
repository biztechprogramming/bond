#!/usr/bin/env python3
"""Test different syntaxes for optional columns in SpacetimeDB SQL."""

import asyncio
from backend.app.core.spacetimedb import StdbClient

async def test_optional_syntax():
    stdb = StdbClient()
    
    test_cases = [
        # Try different syntaxes for optional U64
        ("INSERT INTO work_plans (id, agent_id, conversation_id, parent_plan_id, title, status, created_at, updated_at, completed_at) VALUES ('test-1', 'test-agent', 'test-conv', '', 'Test Plan', 'active', 1000, 1000, 1000)", "Regular value"),
        ("INSERT INTO work_plans (id, agent_id, conversation_id, parent_plan_id, title, status, created_at, updated_at, completed_at) VALUES ('test-2', 'test-agent', 'test-conv', '', 'Test Plan', 'active', 1000, 1000, NULL)", "NULL"),
        ("INSERT INTO work_plans (id, agent_id, conversation_id, parent_plan_id, title, status, created_at, updated_at, completed_at) VALUES ('test-3', 'test-agent', 'test-conv', '', 'Test Plan', 'active', 1000, 1000, 'none')", "'none' string"),
        ("INSERT INTO work_plans (id, agent_id, conversation_id, parent_plan_id, title, status, created_at, updated_at, completed_at) VALUES ('test-4', 'test-agent', 'test-conv', '', 'Test Plan', 'active', 1000, 1000, 'none()')", "'none()'"),
        ("INSERT INTO work_plans (id, agent_id, conversation_id, parent_plan_id, title, status, created_at, updated_at, completed_at) VALUES ('test-5', 'test-agent', 'test-conv', '', 'Test Plan', 'active', 1000, 1000, 'some(1000)')", "'some(1000)'"),
        ("INSERT INTO work_plans (id, agent_id, conversation_id, parent_plan_id, title, status, created_at, updated_at, completed_at) VALUES ('test-6', 'test-agent', 'test-conv', '', 'Test Plan', 'active', 1000, 1000, '(some: 1000)')", "'(some: 1000)'"),
        ("INSERT INTO work_plans (id, agent_id, conversation_id, parent_plan_id, title, status, created_at, updated_at, completed_at) VALUES ('test-7', 'test-agent', 'test-conv', '', 'Test Plan', 'active', 1000, 1000, '(none: ())')", "'(none: ())'"),
    ]
    
    # First clean up any test data
    try:
        await stdb.query("DELETE FROM work_plans WHERE id LIKE 'test-%'")
    except:
        pass
    
    for sql, description in test_cases:
        print(f"\nTesting {description}:")
        print(f"  SQL: {sql}")
        try:
            result = await stdb.query(sql)
            print(f"  ✓ Success!")
        except Exception as e:
            print(f"  ✗ Error: {e}")
    
    await stdb.close()

if __name__ == "__main__":
    asyncio.run(test_optional_syntax())