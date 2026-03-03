import asyncio
import httpx
from sqlalchemy import text
from backend.app.db.session import get_session_factory

GATEWAY_URL = "http://localhost:18792/api/v1"

async def sync():
    session_factory = get_session_factory()
    async with session_factory() as db:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. Sync Work Plans
            print("Syncing work plans...")
            result = await db.execute(text("SELECT id, agent_id, conversation_id, title, status FROM work_plans"))
            plans = result.mappings().all()
            for p in plans:
                print(f"Plan: {p['title']}")
                payload = {
                    "id": p["id"],
                    "agentId": p["agent_id"],
                    "conversationId": p["conversation_id"],
                    "title": p["title"],
                    "status": p["status"],
                    "createdAt": 0,
                    "updatedAt": 0
                }
                resp = await client.post(f"{GATEWAY_URL}/sync/work-plans", json=payload)
                print(f"Result: {resp.status_code}")

            # 2. Sync Work Items
            print("Syncing work items...")
            result = await db.execute(text("SELECT id, plan_id, title, status, ordinal, notes, files_changed FROM work_items"))
            items = result.mappings().all()
            for i in items:
                print(f"Item: {i['title']}")
                payload = {
                    "id": i["id"],
                    "planId": i["plan_id"],
                    "title": i["title"],
                    "status": i["status"],
                    "ordinal": i["ordinal"],
                    "notes": i["notes"],
                    "filesChanged": i["files_changed"],
                    "createdAt": 0,
                    "updatedAt": 0
                }
                resp = await client.post(f"{GATEWAY_URL}/sync/work-items", json=payload)
                print(f"Result: {resp.status_code}")

if __name__ == "__main__":
    asyncio.run(sync())
