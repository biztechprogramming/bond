import asyncio
from backend.app.core.spacetimedb import get_stdb

async def test():
    client = get_stdb()
    rows = await client.query("SELECT * FROM providers")
    for row in rows:
        print(row)

asyncio.run(test())