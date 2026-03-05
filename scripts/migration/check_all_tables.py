import asyncio
from backend.app.core.spacetimedb import StdbClient

async def check_tables():
    stdb = StdbClient()
    
    tables_to_check = [
        "settings",
        "provider_api_keys", 
        "providers",
        "provider_aliases",
        "llm_models",
        "mcp_servers",
        "agents",
        "agent_workspace_mounts",
        "conversations",
        "conversationMessages",
        "messages"
    ]
    
    for table in tables_to_check:
        try:
            rows = await stdb.query(f"SELECT * FROM {table} LIMIT 1")
            if rows:
                print(f"✓ {table}: exists, has {len(rows)} rows, columns: {list(rows[0].keys()) if rows else 'none'}")
            else:
                print(f"✓ {table}: exists but empty")
        except Exception as e:
            print(f"✗ {table}: error - {e}")
    
    await stdb.close()

if __name__ == "__main__":
    asyncio.run(check_tables())