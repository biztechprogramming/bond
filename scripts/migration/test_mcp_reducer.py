import asyncio
import json
from backend.app.core.spacetimedb import StdbClient

async def test_reducer():
    stdb = StdbClient()
    
    # Test add_mcp_server reducer
    server_id = "test-mcp-reducer"
    
    success = await stdb.call_reducer("add_mcp_server", [
        server_id,
        "Test MCP Server",
        "echo",
        json.dumps(["hello"]),
        json.dumps({}),
        None,  # agentId as None for Option type
    ])
    print(f"add_mcp_server reducer result: {success}")
    
    if success:
        print("Checking if server was added...")
        rows = await stdb.query(f"SELECT * FROM mcp_servers WHERE id = '{server_id}'")
        if rows:
            print(f"Server added: {rows[0]}")
            # Clean up
            await stdb.query(f"DELETE FROM mcp_servers WHERE id = '{server_id}'")
    
    await stdb.close()

if __name__ == "__main__":
    asyncio.run(test_reducer())