"""Agent turn endpoint — the core chat API."""

from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.agent.loop import agent_turn
from backend.app.db.session import get_db

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentTurnRequest(BaseModel):
    message: str
    history: list[dict[str, str]] | None = None
    stream: bool = False


class AgentTurnResponse(BaseModel):
    response: str


@router.post("/turn")
async def post_agent_turn(req: AgentTurnRequest, db: AsyncSession = Depends(get_db)):
    """Execute an agent turn: send a message, get a response."""
    if req.stream:
        result = await agent_turn(req.message, req.history, stream=True, db=db)

        async def generate():
            async for chunk in result:
                yield f"data: {chunk}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    result = await agent_turn(req.message, req.history, stream=False, db=db)
    return AgentTurnResponse(response=result)
