import asyncio
from backend.app.core.spacetimedb import StdbClient

async def test_sql():
    stdb = StdbClient()
    
    test_queries = [
        "SELECT * FROM agents",
        "SELECT * FROM agents ORDER BY name",
        "SELECT * FROM agents ORDER BY is_default DESC, name",
        "SELECT * FROM agents WHERE is_active = true",
        "SELECT * FROM agents WHERE is_active = true ORDER BY name",
    ]
    
    for query in test_queries:
        print(f"\nTesting: {query}")
        try:
            result = await stdb.query(query)
            print(f"  Success! Returned {len(result)} rows")
        except Exception as e:
            print(f"  Error: {e}")
    
    await stdb.close()

if __name__ == "__main__":
    asyncio.run(test_sql())