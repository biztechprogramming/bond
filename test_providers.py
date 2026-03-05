import asyncio
from backend.app.core.spacetimedb import get_stdb

async def test():
    client = get_stdb()
    rows = await client.query("SELECT id, display_name, is_enabled FROM providers ORDER BY display_name")
    for row in rows:
        print(row)

asyncio.run(test())