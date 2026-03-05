import asyncio
from backend.app.core.spacetimedb import StdbClient

async def test_reducer():
    stdb = StdbClient()
    
    # Test with ALL strings
    success = await stdb.call_reducer("add_agent", [
        "test-agent-1",
        "Test Agent",
        "Test Display",
        "Test system prompt",
        "gemini/gemini-3-flash-preview",
        "claude-sonnet-4-6",
        "[]",
        "true",  # isDefault as string
        "true",  # isActive as string (guessing)
        "0",     # createdAt as string (guessing)
    ])
    print(f"Test with 10 string args: {success}")
    
    # Check error message
    if not success:
        # Try to get more info
        print("Trying to query agents table to see schema...")
        rows = await stdb.query("SELECT * FROM agents LIMIT 1")
        if rows:
            print(f"Agents table columns: {list(rows[0].keys())}")
        else:
            print("No rows in agents table")
    
    await stdb.close()

if __name__ == "__main__":
    asyncio.run(test_reducer())