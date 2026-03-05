import asyncio
from backend.app.core.spacetimedb import StdbClient

async def test_column_names():
    stdb = StdbClient()
    
    # Test querying conversations table
    rows = await stdb.query("SELECT * FROM conversations LIMIT 1")
    print(f"Number of rows: {len(rows)}")
    if rows:
        print(f"First row keys: {list(rows[0].keys())}")
        print(f"First row: {rows[0]}")
    
    # Test querying with specific columns
    rows2 = await stdb.query("SELECT id, agent_id, is_active FROM conversations LIMIT 1")
    if rows2:
        print(f"\nSpecific query keys: {list(rows2[0].keys())}")
        print(f"Specific query row: {rows2[0]}")
    
    await stdb.close()

if __name__ == "__main__":
    asyncio.run(test_column_names())