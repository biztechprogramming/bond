#!/usr/bin/env python3
"""Test different values for optional parameters in reducers."""

import asyncio
import httpx
import json

async def test_optional_args():
    base_url = "http://localhost:18787"
    module = "bond-core-v2"
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        print("=== Testing optional arguments for import_work_plan ===\n")
        
        # Test different values for optional completedAt parameter
        test_cases = [
            # (completed_at_value, description)
            (1000, "Regular value (1000)"),
            (0, "Zero value (0)"),
            (None, "None (Python null)"),
            ("null", "String 'null'"),
            ("undefined", "String 'undefined'"),
            ("", "Empty string"),
        ]
        
        for completed_at_val, description in test_cases:
            print(f"\nTesting: {description}")
            
            # Build args - all required params plus optional completedAt
            args = [
                f"test-plan-opt-{description.replace(' ', '-')}",
                "test-agent",
                "test-conv",
                "",
                f"Test plan {description}",
                "active",
                1000,  # createdAt
                1000,  # updatedAt
            ]
            
            # Add completedAt value if not None
            if completed_at_val is not None:
                args.append(completed_at_val)
            # If None, don't append anything (omit the parameter)
            
            reducer = "import_work_plan"
            url = f"{base_url}/v1/database/{module}/call/{reducer}"
            
            print(f"  Args: {args}")
            print(f"  Args JSON: {json.dumps(args)}")
            
            try:
                resp = await client.post(url, headers={"Content-Type": "application/json"}, json=args)
                print(f"  Status: {resp.status_code}")
                if resp.status_code == 200:
                    print(f"  ✓ HTTP 200 OK")
                else:
                    print(f"  ✗ HTTP {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                print(f"  ✗ Exception: {e}")
        
        # Also test with import_work_item which has multiple optional params
        print("\n\n=== Testing import_work_item ===")
        
        # Test with all params (including optional ones as 0)
        args_all = [
            "test-item-opt-all",
            "test-plan-id",
            "Test item",
            "new",
            0,  # ordinal
            "{}",  # contextSnapshot
            "[]",  # notes
            "[]",  # filesChanged
            0,  # startedAt (optional)
            0,  # completedAt (optional)
            1000,  # createdAt
            1000,  # updatedAt
            "",  # description (has default)
        ]
        
        reducer = "import_work_item"
        url = f"{base_url}/v1/database/{module}/call/{reducer}"
        
        print(f"\nTesting with all params (including 0 for optional):")
        print(f"  Args: {args_all[:5]}... [{len(args_all)} total]")
        
        try:
            resp = await client.post(url, headers={"Content-Type": "application/json"}, json=args_all)
            print(f"  Status: {resp.status_code}")
            if resp.status_code == 200:
                print(f"  ✓ HTTP 200 OK")
            else:
                print(f"  ✗ HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            print(f"  ✗ Exception: {e}")
        
        # Test with fewer params (omitting optional ones)
        print(f"\nTesting with fewer params (omitting optional):")
        # The reducer has 13 params total, but some are optional or have defaults
        # Let's try with just the required ones
        args_minimal = [
            "test-item-opt-min",
            "test-plan-id",
            "Test item minimal",
            "new",
            0,  # ordinal
            "{}",  # contextSnapshot
            "[]",  # notes
            "[]",  # filesChanged
            # startedAt omitted (optional)
            # completedAt omitted (optional)
            1000,  # createdAt
            1000,  # updatedAt
            "",  # description (has default)
        ]
        
        print(f"  Args: {args_minimal[:5]}... [{len(args_minimal)} total]")
        
        try:
            resp = await client.post(url, headers={"Content-Type": "application/json"}, json=args_minimal)
            print(f"  Status: {resp.status_code}")
            if resp.status_code == 200:
                print(f"  ✓ HTTP 200 OK")
            else:
                print(f"  ✗ HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            print(f"  ✗ Exception: {e}")

if __name__ == "__main__":
    asyncio.run(test_optional_args())