import asyncio
from backend.app.core.spacetimedb import get_stdb

async def test():
    client = get_stdb()
    rows = await client.query("""
        SELECT p.id, CASE WHEN pak.provider_id IS NOT NULL THEN true ELSE false END as has_key
        FROM providers p
        LEFT JOIN provider_api_keys pak ON p.id = pak.provider_id
        WHERE p.is_enabled = true
    """)
    for row in rows:
        print(row)

asyncio.run(test())