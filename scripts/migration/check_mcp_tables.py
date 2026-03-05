import asyncio
from backend.app.core.spacetimedb import StdbClient

async def check_mcp():
    stdb = StdbClient()
    
    # Check mcp_servers table
    print("Checking mcp_servers table in SpacetimeDB:")
    try:
        rows = await stdb.query("SELECT * FROM mcp_servers LIMIT 1")
        if rows:
            print(f"Table exists, columns: {list(rows[0].keys())}")
        else:
            print("Table exists but has no rows")
    except Exception as e:
        print(f"Error: {e}")
    
    await stdb.close()

if __name__ == "__main__":
    asyncio.run(check_mcp())