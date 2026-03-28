"""Lightweight LLM completion endpoint — no agent loop, no conversation."""

from __future__ import annotations
import logging
from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.app.agent.llm import chat_completion

logger = logging.getLogger("bond.llm")
router = APIRouter(prefix="/llm", tags=["llm"])


class CompletionRequest(BaseModel):
    messages: list[dict] = Field(..., description="Chat messages [{role, content}]")
    max_tokens: int = Field(4096, ge=1, le=32768)
    temperature: float = Field(0.1, ge=0.0, le=2.0)


class CompletionResponse(BaseModel):
    content: str


@router.post("/complete", response_model=CompletionResponse)
async def complete(req: CompletionRequest):
    """Simple LLM completion — calls chat_completion directly, no agent loop."""
    logger.info("LLM complete: %d messages, max_tokens=%d", len(req.messages), req.max_tokens)
    try:
        result = await chat_completion(
            req.messages,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            stream=False,
        )
        return CompletionResponse(content=result)
    except Exception as e:
        logger.error("LLM completion failed: %s", e)
        raise
