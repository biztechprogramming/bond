"""Notify tool — stub for Phase 2."""

from __future__ import annotations

from typing import Any


async def handle_notify(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    return {"status": "not_configured", "message": "Notifications coming in Phase 2."}
