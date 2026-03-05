import asyncio
import time
from backend.app.core.spacetimedb import StdbClient

async def test_reducer():
    stdb = StdbClient()
    
    # Get current timestamp in milliseconds
    created_at = int(time.time() * 1000)
    
    # Try ALL as strings
    success = await stdb.call_reducer("add_agent", [
        "test-agent-1",  # id
        "Test Agent",    # name
        "Test Display",  # displayName
        "Test system prompt",  # systemPrompt
        "gemini/gemini-3-flash-preview",  # model
        "claude-sonnet-4-6",  # utilityModel
        "[]",  # tools
        "1",   # isDefault as string
        "1",   # isActive as string
        str(created_at),  # createdAt as string
    ])
    print(f"Test with ALL strings: {success}")
    
    # If that doesn't work, try mixed based on latest error
    if not success:
        success = await stdb.call_reducer("add_agent", [
            "test-agent-2",
            "Test Agent 2",
            "Test Display 2",
            "Test system prompt 2",
            "gemini/gemini-3-flash-preview",
            "claude-sonnet-4-6",
            "[]",
            "1",  # isDefault as string
            1,    # isActive as u32
            created_at,  # createdAt as u64
        ])
        print(f"Test with mixed types: {success}")
    
    await stdb.close()

if __name__ == "__main__":
    asyncio.run(test_reducer())