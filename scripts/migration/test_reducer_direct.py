#!/usr/bin/env python3
"""Test reducers directly with HTTP calls."""

import asyncio
import httpx
import json

async def test_reducer_direct():
    base_url = "http://localhost:18787"
    module = "bond-core-v2"
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        print("=== Testing reducers directly ===\n")
        
        # Test both naming conventions
        test_cases = [
            ("addConversationMessage", ["test-msg-direct-1", "test-conv-direct", "user", "Direct test", "", "", 0, "delivered"], "camelCase"),
            ("add_conversation_message", ["test-msg-direct-2", "test-conv-direct", "user", "Direct test", "", "", 0, "delivered"], "snake_case"),
            ("createConversation", ["test-conv-direct-1", "test-agent", "webchat", "Direct test conv"], "camelCase"),
            ("create_conversation", ["test-conv-direct-2", "test-agent", "webchat", "Direct test conv"], "snake_case"),
        ]
        
        for reducer, args, description in test_cases:
            print(f"\nTesting {description}: {reducer}")
            url = f"{base_url}/v1/database/{module}/call/{reducer}"
            print(f"URL: {url}")
            print(f"Args: {args}")
            
            try:
                resp = await client.post(url, headers={"Content-Type": "application/json"}, json=args)
                print(f"Status: {resp.status_code}")
                print(f"Response: {resp.text[:200]}...")
                
                if resp.status_code == 200:
                    print("✓ HTTP 200 OK")
                else:
                    print("✗ HTTP Error")
            except Exception as e:
                print(f"✗ Exception: {e}")
        
        # Also test the importWorkPlan reducer which we need for migration
        print("\n\n=== Testing importWorkPlan reducer (for migration) ===")
        reducer = "importWorkPlan"
        args = [
            "test-plan-import-1",
            "test-agent",
            "test-conv-direct",
            "",
            "Test imported plan",
            "active",
            1000,  # createdAt
            1000,  # updatedAt
            # completedAt is optional - what value should we use?
        ]
        
        print(f"\nTesting {reducer}")
        url = f"{base_url}/v1/database/{module}/call/{reducer}"
        print(f"URL: {url}")
        print(f"Args: {args}")
        
        try:
            resp = await client.post(url, headers={"Content-Type": "application/json"}, json=args)
            print(f"Status: {resp.status_code}")
            print(f"Response: {resp.text[:200]}...")
        except Exception as e:
            print(f"✗ Exception: {e}")
        
        # Test with completedAt as None/null
        print("\n\n=== Testing importWorkPlan with null completedAt ===")
        args_with_null = [
            "test-plan-import-2",
            "test-agent",
            "test-conv-direct",
            "",
            "Test imported plan 2",
            "active",
            1000,  # createdAt
            1000,  # updatedAt
            None,  # completedAt - optional
        ]
        
        print(f"\nTesting {reducer} with None for optional column")
        print(f"Args: {args_with_null}")
        
        try:
            resp = await client.post(url, headers={"Content-Type": "application/json"}, json=args_with_null)
            print(f"Status: {resp.status_code}")
            print(f"Response: {resp.text[:200]}...")
        except Exception as e:
            print(f"✗ Exception: {e}")

if __name__ == "__main__":
    asyncio.run(test_reducer_direct())