#!/usr/bin/env python3
"""Test if SpacetimeDB accepts snake_case reducer names."""

import asyncio
from backend.app.core.spacetimedb import StdbClient

async def test_reducer_names():
    stdb = StdbClient()
    
    print("=== Testing reducer name conversion ===\n")
    
    test_cases = [
        # (reducer_name, args, description)
        ("addConversationMessage", ["test-msg-1", "test-conv", "user", "Test message", "", "", 0, "delivered"], "camelCase (TypeScript name)"),
        ("add_conversation_message", ["test-msg-2", "test-conv", "user", "Test message", "", "", 0, "delivered"], "snake_case (Python name)"),
        ("createConversation", ["test-conv-1", "test-agent", "webchat", "Test conversation"], "camelCase"),
        ("create_conversation", ["test-conv-2", "test-agent", "webchat", "Test conversation"], "snake_case"),
    ]
    
    # Clean up first
    print("1. Cleaning up test data...")
    cleanup_reducers = [
        ("deleteConversationMessage", ["test-msg-1", "test-conv"]),
        ("deleteConversationMessage", ["test-msg-2", "test-conv"]),
        ("deleteConversation", ["test-conv-1"]),
        ("deleteConversation", ["test-conv-2"]),
    ]
    
    for reducer, args in cleanup_reducers:
        try:
            await stdb.call_reducer(reducer, args)
        except:
            pass  # Ignore errors
    
    print("2. Testing reducer calls:")
    results = []
    
    for reducer, args, description in test_cases:
        print(f"\n   Testing: {description}")
        print(f"   Reducer: {reducer}")
        print(f"   Args: {args}")
        try:
            success = await stdb.call_reducer(reducer, args)
            if success:
                print(f"   ✓ Success!")
                results.append((reducer, description, True, None))
            else:
                print(f"   ✗ Failed (returned False)")
                results.append((reducer, description, False, "Returned False"))
        except Exception as e:
            error_str = str(e)
            if len(error_str) > 100:
                error_str = error_str[:100] + "..."
            print(f"   ✗ Error: {error_str}")
            results.append((reducer, description, False, error_str))
    
    print("\n=== Results ===")
    successful = [r for r in results if r[2]]
    failed = [r for r in results if not r[2]]
    
    print(f"Successful: {len(successful)}")
    for reducer, desc, _, _ in successful:
        print(f"  - {desc}: {reducer}")
    
    print(f"\nFailed: {len(failed)}")
    for reducer, desc, _, error in failed:
        print(f"  - {desc}: {reducer}")
        if error:
            print(f"    Error: {error}")
    
    # Check what was actually created
    print("\n3. Checking what was actually created...")
    try:
        # Check conversations
        convs = await stdb.query("SELECT id FROM conversations WHERE id LIKE 'test-conv-%'")
        print(f"   Conversations created: {len(convs)}")
        for conv in convs:
            print(f"   - {conv['id']}")
        
        # Check messages
        msgs = await stdb.query("SELECT id FROM conversation_messages WHERE id LIKE 'test-msg-%'")
        print(f"   Messages created: {len(msgs)}")
        for msg in msgs:
            print(f"   - {msg['id']}")
    except Exception as e:
        print(f"   Error checking: {e}")
    
    await stdb.close()

if __name__ == "__main__":
    asyncio.run(test_reducer_names())