import asyncio
from backend.app.core.spacetimedb import get_stdb

async def test():
    client = get_stdb()
    # First, ensure provider exists
    rows = await client.query("SELECT id FROM providers WHERE id = 'openai'")
    print("Provider exists:", rows)
    # Set a dummy encrypted key
    encrypted = "encrypted_dummy"
    key_type = "bearer"
    import time
    now = int(time.time() * 1000)
    success = await client.call_reducer("set_provider_api_key", ["openai", encrypted, key_type, now, now])
    print("Reducer success:", success)
    # Verify insertion
    keys = await client.query("SELECT * FROM provider_api_keys")
    print("Keys:", keys)

asyncio.run(test())