"""Minimal agent loop — receive message, call LLM, return response.

Sprint 1: No tools, no RAG, no multi-agent. Just a direct LLM call.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

from backend.app.agent.llm import chat_completion

logger = logging.getLogger("bond.agent.loop")

SYSTEM_PROMPT = """\
You are Bond, a helpful personal AI assistant. You are running locally on the \
user's machine. Be concise, helpful, and friendly. If you don't know something, \
say so directly.\
"""


async def agent_turn(
    user_message: str,
    history: list[dict[str, str]] | None = None,
    *,
    system_prompt: str | None = None,
    stream: bool = False,
) -> str | AsyncIterator[str]:
    """Execute a single agent turn: user message -> LLM -> response.

    Args:
        user_message: The user's input message.
        history: Optional conversation history (list of {role, content} dicts).
        system_prompt: Override the default system prompt.
        stream: If True, returns an async iterator of text chunks.

    Returns:
        The assistant's response text, or an async iterator for streaming.
    """
    messages = []

    # System prompt
    messages.append({
        "role": "system",
        "content": system_prompt or SYSTEM_PROMPT,
    })

    # History
    if history:
        messages.extend(history)

    # Current user message
    messages.append({
        "role": "user",
        "content": user_message,
    })

    logger.info("Agent turn: %d messages in context", len(messages))

    return await chat_completion(messages, stream=stream)
