import asyncio
import json
import time
from backend.app.core.spacetimedb import StdbClient

async def check_schema():
    stdb = StdbClient()
    
    # Try to insert a test MCP server with empty string for agent_id
    server_id = "test-mcp-server"
    created_at = int(time.time() * 1000)
    
    sql = f"""
    INSERT INTO mcp_servers (id, name, command, args, env, enabled, created_at, updated_at, agent_id)
    VALUES (
        '{server_id}',
        'Test MCP Server',
        'echo',
        '{json.dumps(["hello"])}',
        '{json.dumps({})}',
        true,
        {created_at},
        {created_at},
        ''
    )
    """
    
    print("Trying to insert test MCP server with empty agent_id...")
    try:
        result = await stdb.query(sql)
        print(f"Insert result: {result}")
        
        # Check if it was inserted
        rows = await stdb.query(f"SELECT * FROM mcp_servers WHERE id = '{server_id}'")
        if rows:
            print(f"\nSuccess! MCP server added. Columns: {list(rows[0].keys())}")
            print(f"Row: {rows[0]}")
        else:
            print("\nFailed to add MCP server")
            
        # Clean up
        await stdb.query(f"DELETE FROM mcp_servers WHERE id = '{server_id}'")
        
    except Exception as e:
        print(f"Error: {e}")
    
    await stdb.close()

if __name__ == "__main__":
    asyncio.run(check_schema())