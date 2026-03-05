import asyncio
from backend.app.core.spacetimedb import StdbClient

async def check_keys():
    stdb = StdbClient()
    
    print("Checking provider_api_keys:")
    rows = await stdb.query("SELECT * FROM provider_api_keys")
    for row in rows:
        print(f"  Provider: {row['provider_id']}")
        print(f"  Encrypted value: {row['encrypted_value'][:50]}...")
        print(f"  Key type: {row['key_type']}")
        print(f"  Created at: {row['created_at']}")
        print()
    
    print("\nChecking providers:")
    rows = await stdb.query("SELECT * FROM providers")
    for row in rows:
        print(f"  ID: {row['id']}")
        print(f"  Display name: {row['display_name']}")
        print(f"  LiteLLM prefix: {row['litellm_prefix']}")
        print(f"  Is enabled: {row['is_enabled']}")
        print()
    
    print("\nChecking settings:")
    rows = await stdb.query("SELECT * FROM settings")
    for row in rows:
        print(f"  Key: {row['key']}")
        print(f"  Value: {row['value'][:50]}..." if len(row['value']) > 50 else f"  Value: {row['value']}")
        print(f"  Key type: {row.get('key_type', 'N/A')}")
        print()
    
    await stdb.close()

if __name__ == "__main__":
    asyncio.run(check_keys())