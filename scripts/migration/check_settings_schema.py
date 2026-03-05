import asyncio
from backend.app.core.spacetimedb import StdbClient

async def check_schema():
    stdb = StdbClient()
    
    # Try to insert a test setting to see what columns are expected
    print("Testing settings table schema...")
    
    # Try with different column combinations
    test_sqls = [
        "INSERT INTO settings (key, value) VALUES ('test-key', 'test-value')",
        "INSERT INTO settings (key, value, created_at) VALUES ('test-key2', 'test-value2', 1234567890000)",
        "INSERT INTO settings (key, value, created_at, updated_at) VALUES ('test-key3', 'test-value3', 1234567890000, 1234567890000)",
    ]
    
    for i, sql in enumerate(test_sqls):
        print(f"\nTrying: {sql}")
        try:
            result = await stdb.query(sql)
            print(f"  Result: {result}")
            # Clean up
            await stdb.query(f"DELETE FROM settings WHERE key = 'test-key{i+1}'")
        except Exception as e:
            print(f"  Error: {e}")
    
    # Check what's in settings table
    print("\nCurrent settings in table:")
    rows = await stdb.query("SELECT * FROM settings")
    for row in rows:
        print(f"  {row}")
    
    await stdb.close()

if __name__ == "__main__":
    asyncio.run(check_schema())