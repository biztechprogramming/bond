import asyncio
from backend.app.core.spacetimedb import StdbClient

async def check_agents():
    stdb = StdbClient()
    
    # Check agents in SpacetimeDB
    print("Checking agents in SpacetimeDB:")
    rows = await stdb.query("SELECT id, name, display_name, is_default FROM agents")
    print(f"Found {len(rows)} agents:")
    for row in rows:
        print(f"  ID: {row.get('id')}, Name: {row.get('name')}, Display: {row.get('display_name')}, Default: {row.get('is_default')}")
    
    await stdb.close()

if __name__ == "__main__":
    asyncio.run(check_agents())