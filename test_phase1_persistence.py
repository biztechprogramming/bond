import httpx
import asyncio
import os
import json

async def test_persistence_connection():
    # Simulate worker environment variables
    # Default to 18792 which is the design port we just verified
    gateway_url = os.environ.get("BOND_GATEWAY_URL", "http://localhost:18792")
    
    print(f"Testing connection to Gateway at: {gateway_url}")
    
    async with httpx.AsyncClient(base_url=f"{gateway_url}") as client:
        # 1. Test Health
        try:
            resp = await client.get("/health") 
            print(f"Health check: {resp.status_code} - {resp.json()}")
        except Exception as e:
            print(f"Health check failed: {e}")

    async with httpx.AsyncClient(base_url=f"{gateway_url}/api/v1") as client:
        # 2. Test Message Shadow-Write
        msg_payload = {
            "agentId": "test-agent",
            "sessionId": "test-session",
            "role": "user",
            "content": "Hello persistence!"
        }
        try:
            resp = await client.post("/messages", json=msg_payload)
            print(f"Message write: {resp.status_code} - {resp.json()}")
        except Exception as e:
            print(f"Message write failed: {e}")

        # 3. Test Tool Log Shadow-Write
        tool_payload = {
            "agentId": "test-agent",
            "sessionId": "test-session",
            "toolName": "test_tool",
            "input": {"arg1": "val1"},
            "output": {"result": "ok"},
            "duration": 0.5
        }
        try:
            resp = await client.post("/tool-logs", json=tool_payload)
            print(f"Tool log write: {resp.status_code} - {resp.json()}")
        except Exception as e:
            print(f"Tool log write failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_persistence_connection())
