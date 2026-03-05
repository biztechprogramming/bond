import asyncio
from backend.app.core.spacetimedb import StdbClient

async def check_schema():
    stdb = StdbClient()
    
    # Try to get schema information
    # SpacetimeDB doesn't have SHOW TABLES or DESCRIBE, but we can query with LIMIT 0
    # to get the schema without data
    rows = await stdb.query("SELECT * FROM agents LIMIT 0")
    print("Query with LIMIT 0 returns empty rows, but we can't see schema from Python")
    
    # Try a different approach - query information schema if it exists
    try:
        info = await stdb.query("SELECT * FROM information_schema.columns WHERE table_name = 'agents'")
        print(f"Information schema: {info}")
    except:
        print("No information_schema table")
    
    # Try to insert with all possible columns from SQLite schema
    import time
    created_at = int(time.time() * 1000)
    
    # From SQLite schema we have: id, name, display_name, system_prompt, model, sandbox_image, tools, 
    # max_iterations, auto_rag, auto_rag_limit, is_default, is_active, created_at, updated_at, utility_model
    # But SpacetimeDB might have different columns
    
    # Let's try to guess based on the 12 columns error
    # The TypeScript schema shows: id, name, displayName, systemPrompt, model, utilityModel, tools, isActive, isDefault, createdAt
    # That's 10 columns. But we need 12.
    # Maybe also: sandbox_image, max_iterations?
    
    sql = f"""
    INSERT INTO agents (id, name, display_name, system_prompt, model, utility_model, tools, sandbox_image, max_iterations, is_default, is_active, created_at)
    VALUES (
        'test-full-agent',
        'Full Test Agent',
        'Full Test Display',
        'Test system prompt',
        'gemini/gemini-3-flash-preview',
        'claude-sonnet-4-6',
        '[]',
        '',
        25,
        true,
        true,
        {created_at}
    )
    """
    
    print(f"\nTrying SQL with 12 columns...")
    result = await stdb.query(sql)
    print(f"SQL insert result: {result}")
    
    # Check if it worked
    rows = await stdb.query("SELECT * FROM agents WHERE id = 'test-full-agent'")
    if rows:
        print(f"\nSuccess! Agent added. Columns: {list(rows[0].keys())}")
        print(f"Row: {rows[0]}")
    else:
        print("\nFailed to add agent")
    
    await stdb.close()

if __name__ == "__main__":
    asyncio.run(check_schema())