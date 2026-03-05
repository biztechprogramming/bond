import asyncio
import time
from backend.app.core.spacetimedb import StdbClient

async def test_sql_insert():
    stdb = StdbClient()
    
    # Try to insert directly with SQL
    created_at = int(time.time() * 1000)
    
    sql = f"""
    INSERT INTO agents (id, name, display_name, system_prompt, model, utility_model, tools, is_default, is_active, created_at)
    VALUES (
        'test-sql-agent',
        'SQL Test Agent',
        'SQL Test Display',
        'Test system prompt',
        'gemini/gemini-3-flash-preview',
        'claude-sonnet-4-6',
        '[]',
        true,
        true,
        {created_at}
    )
    """
    
    print(f"Trying SQL: {sql[:100]}...")
    result = await stdb.query(sql)
    print(f"SQL insert result: {result}")
    
    # Check if it worked
    rows = await stdb.query("SELECT * FROM agents WHERE id = 'test-sql-agent'")
    print(f"Query result: {rows}")
    
    await stdb.close()

if __name__ == "__main__":
    asyncio.run(test_sql_insert())