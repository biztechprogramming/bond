import asyncio
from backend.app.core.spacetimedb import get_stdb

async def test():
    client = get_stdb()
    rows = await client.query("""
        SELECT p.id, p.display_name, p.litellm_prefix
        FROM providers p
        WHERE p.is_enabled = true
    """)
    for row in rows:
        print(row)

asyncio.run(test())