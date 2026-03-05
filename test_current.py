import asyncio
from backend.app.core.spacetimedb import get_stdb

async def test():
    client = get_stdb()
    providers = await client.query("SELECT id FROM providers WHERE is_enabled = true")
    keys = await client.query("SELECT provider_id FROM provider_api_keys")
    key_set = {row["provider_id"] for row in keys}
    keys_set = {row["id"]: row["id"] in key_set for row in providers}
    print(keys_set)

asyncio.run(test())