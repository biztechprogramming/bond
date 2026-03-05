import asyncio
from backend.app.core.spacetimedb import StdbClient

async def test_reducer():
    stdb = StdbClient()
    
    # Test with string for boolean
    success = await stdb.call_reducer("add_agent", [
        "test-agent-1",
        "Test Agent",
        "Test Display",
        "Test system prompt",
        "gemini/gemini-3-flash-preview",
        "claude-sonnet-4-6",
        "[]",
        "true",  # Try string instead of boolean
    ])
    print(f"Test 1 (string boolean): {success}")
    
    # Test with integer for boolean
    success = await stdb.call_reducer("add_agent", [
        "test-agent-2",
        "Test Agent 2",
        "Test Display 2",
        "Test system prompt 2",
        "gemini/gemini-3-flash-preview",
        "claude-sonnet-4-6",
        "[]",
        1,  # Try integer instead of boolean
    ])
    print(f"Test 2 (integer boolean): {success}")
    
    # Test with actual boolean
    success = await stdb.call_reducer("add_agent", [
        "test-agent-3",
        "Test Agent 3",
        "Test Display 3",
        "Test system prompt 3",
        "gemini/gemini-3-flash-preview",
        "claude-sonnet-4-6",
        "[]",
        True,  # Actual boolean
    ])
    print(f"Test 3 (actual boolean): {success}")
    
    await stdb.close()

if __name__ == "__main__":
    asyncio.run(test_reducer())