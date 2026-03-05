#!/usr/bin/env python3
"""Test sum type syntax for optional parameters."""

import asyncio
import httpx
import json

async def test_sum_type():
    base_url = "http://localhost:18787"
    module = "bond-core-v2"
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        print("=== Testing sum type syntax ===\n")
        
        # Test different representations of sum types
        test_cases = [
            # (completed_at_value, description)
            ({"some": 1000}, '{"some": 1000}'),
            ({"Some": 1000}, '{"Some": 1000}'),
            ({"SOME": 1000}, '{"SOME": 1000}'),
            ({"none": []}, '{"none": []}'),
            ({"none": {}}, '{"none": {}}'),
            ({"none": None}, '{"none": null}'),
            ({"None": []}, '{"None": []}'),
            ({"NONE": []}, '{"NONE": []}'),
            (["some", 1000], '["some", 1000]'),
            (["none", []], '["none", []]'),
            ("some(1000)", '"some(1000)"'),
            ("none()", '"none()"'),
            ("(some: 1000)", '"(some: 1000)"'),
            ("(none: ())", '"(none: ())"'),
        ]
        
        for completed_at_val, description in test_cases:
            print(f"\nTesting: {description}")
            
            args = [
                f"test-plan-sum-{hash(description) % 1000}",  # Unique ID
                "test-agent",
                "test-conv",
                "",
                f"Test plan {description}",
                "active",
                1000,  # createdAt
                1000,  # updatedAt
                completed_at_val,  # completedAt as sum type
            ]
            
            reducer = "import_work_plan"
            url = f"{base_url}/v1/database/{module}/call/{reducer}"
            
            print(f"  Args JSON: {json.dumps(args)[:200]}...")
            
            try:
                resp = await client.post(url, headers={"Content-Type": "application/json"}, json=args)
                print(f"  Status: {resp.status_code}")
                if resp.status_code == 200:
                    print(f"  ✓ HTTP 200 OK")
                    # Check if it was actually created
                    check_url = f"{base_url}/v1/database/{module}/sql"
                    check_sql = f"SELECT id FROM work_plans WHERE id = '{args[0]}'"
                    check_resp = await client.post(check_url, headers={"Content-Type": "application/json"}, content=check_sql)
                    if check_resp.status_code == 200:
                        data = check_resp.json()
                        if data and data[0].get("rows"):
                            print(f"  ✓ Actually created in database")
                        else:
                            print(f"  ? Created but not found in database")
                else:
                    print(f"  ✗ HTTP {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                print(f"  ✗ Exception: {e}")

if __name__ == "__main__":
    asyncio.run(test_sum_type())