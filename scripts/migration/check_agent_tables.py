import asyncio
from backend.app.core.spacetimedb import StdbClient

async def check_tables():
    stdb = StdbClient()
    
    # Check agent_workspace_mounts
    print("Checking agent_workspace_mounts table:")
    rows = await stdb.query("SELECT * FROM agent_workspace_mounts LIMIT 1")
    if rows:
        print(f"Columns: {list(rows[0].keys())}")
    else:
        print("No rows or table doesn't exist")
    
    # Check agent_channels
    print("\nChecking agent_channels table:")
    rows = await stdb.query("SELECT * FROM agent_channels LIMIT 1")
    if rows:
        print(f"Columns: {list(rows[0].keys())}")
    else:
        print("No rows or table doesn't exist")
    
    await stdb.close()

if __name__ == "__main__":
    asyncio.run(check_tables())