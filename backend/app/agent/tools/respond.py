"""Respond tool — returns a message to the user (terminal, ends loop)."""

from __future__ import annotations

from typing import Any


async def handle_respond(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Return the message to the user. The loop treats this as terminal."""
    return {
        "message": arguments.get("message", ""),
        "_terminal": True,
    }
