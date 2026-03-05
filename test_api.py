import asyncio
from fastapi.testclient import TestClient
from backend.app.main import app

client = TestClient(app)

def test_get_llm_providers():
    response = client.get("/api/v1/settings/llm/providers")
    print(response.status_code)
    print(response.json())

if __name__ == "__main__":
    test_get_llm_providers()