#!/usr/bin/env python3
"""Test import reducers for migration."""

import asyncio
import httpx

async def test_import_reducers():
    base_url = "http://localhost:18787"
    module = "bond-core-v2"
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        print("=== Testing import reducers ===\n")
        
        # Test all possible naming conventions for import reducers
        import_reducers = [
            "importWorkPlan",      # camelCase (TypeScript)
            "import_work_plan",    # snake_case
            "importWorkItem",      # camelCase
            "import_work_item",    # snake_case
            "importConversation",  # camelCase
            "import_conversation", # snake_case
            "importConversationMessage",  # camelCase
            "import_conversation_message", # snake_case
        ]
        
        for reducer in import_reducers:
            print(f"\nTesting reducer: {reducer}")
            url = f"{base_url}/v1/database/{module}/call/{reducer}"
            
            # Simple test args
            args = ["test-id", "test-data"]
            
            try:
                resp = await client.post(url, headers={"Content-Type": "application/json"}, json=args)
                if resp.status_code == 200:
                    print(f"  ✓ HTTP 200 (exists)")
                elif resp.status_code == 404:
                    print(f"  ✗ HTTP 404 (not found)")
                else:
                    print(f"  ? HTTP {resp.status_code}: {resp.text[:100]}")
            except Exception as e:
                print(f"  ✗ Exception: {e}")

if __name__ == "__main__":
    asyncio.run(test_import_reducers())