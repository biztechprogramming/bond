import httpx
import asyncio
import os
import json

async def test_persistence_connection():
    # Simulate worker environment variables
    gateway_url = os.environ.get("BOND_GATEWAY_URL", "http://localhost:18792")
    
    print(f"Testing connection to Gateway at: {gateway_url}/api/v1")
    
    async with httpx.AsyncClient(base_url=f"{gateway_url}/api/v1") as client:
        # 1. Test Health
        try:
            # Note: Health is at root, not /api/v1/health in the gateway express app
            # but let's check what we implemented in server.ts
            resp = await client.get("/health") 
            print(f"Health check: {resp.status_code} - {resp.json()}")
        except Exception as e:
            print(f"Health check failed: {e}")

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
