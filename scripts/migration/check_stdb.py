import asyncio
import httpx
import json

async def check_stdb():
    async with httpx.AsyncClient() as client:
        # Try to get schema
        try:
            resp = await client.get("http://localhost:18787/v1/database/bond-core-v2/schema")
            print("Schema response:", resp.status_code)
            if resp.status_code == 200:
                print(json.dumps(resp.json(), indent=2))
        except Exception as e:
            print(f"Error getting schema: {e}")
        
        # Try to query for tables
        try:
            # Try different SQL queries
            queries = [
                "SELECT * FROM conversations LIMIT 1",
                "SELECT * FROM conversationMessages LIMIT 1",
                "SELECT * FROM messages LIMIT 1",
                "SELECT * FROM agents LIMIT 1",
            ]
            for query in queries:
                print(f"\nTrying query: {query}")
                resp = await client.post(
                    "http://localhost:18787/v1/database/bond-core-v2/sql",
                    content=query,
                    headers={"Content-Type": "application/json"}
                )
                print(f"Response: {resp.status_code}")
                if resp.status_code == 200:
                    data = resp.json()
                    print(f"Data: {json.dumps(data, indent=2)}")
        except Exception as e:
            print(f"Error querying: {e}")

if __name__ == "__main__":
    asyncio.run(check_stdb())