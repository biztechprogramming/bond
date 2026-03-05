import asyncio
import time
from backend.app.core.spacetimedb import StdbClient

async def test_reducer():
    stdb = StdbClient()
    
    # Get current timestamp in milliseconds
    created_at = int(time.time() * 1000)
    
    # Test with correct types based on error messages
    success = await stdb.call_reducer("add_agent", [
        "test-agent-1",  # id (string)
        "Test Agent",    # name (string)
        "Test Display",  # displayName (string)
        "Test system prompt",  # systemPrompt (string)
        "gemini/gemini-3-flash-preview",  # model (string)
        "claude-sonnet-4-6",  # utilityModel (string)
        "[]",  # tools (string)
        1,     # isDefault (u32 - 1 for true, 0 for false)
        1,     # isActive (u32 - 1 for true, 0 for false)
        created_at,  # createdAt (u64)
    ])
    print(f"Test with correct types: {success}")
    
    if success:
        print("Success! Checking if agent was added...")
        rows = await stdb.query("SELECT * FROM agents WHERE id = 'test-agent-1'")
        if rows:
            print(f"Agent added: {rows[0]}")
    
    await stdb.close()

if __name__ == "__main__":
    asyncio.run(test_reducer())