"""Shared memory API — stub for C6 shared memory persistence.

The gateway forwards memory promotion events from container workers here.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from ulid import ULID

logger = logging.getLogger("bond.api.memory")

router = APIRouter(prefix="/shared-memories", tags=["shared-memories"])


class MemoryPromotionRequest(BaseModel):
    agent_id: str
    memory_id: str
    type: str
    content: str
    summary: str = ""
    source_type: str = "agent"
    entities: list[str] = []


@router.post("", status_code=202)
async def promote_memory(body: MemoryPromotionRequest):
    """Accept a memory promotion event from the gateway.

    Returns 202 Accepted. The memory is logged but not yet persisted.
    """
    logger.info(
        "Memory promotion received: agent=%s type=%s memory_id=%s content_length=%d",
        body.agent_id,
        body.type,
        body.memory_id,
        len(body.content),
    )

    # TODO(C6): persist to shared_memories table
    shared_memory_id = str(ULID())

    return {
        "status": "accepted",
        "shared_memory_id": shared_memory_id,
    }
