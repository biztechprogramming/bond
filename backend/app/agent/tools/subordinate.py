"""Call subordinate tool — stub for Phase 2."""

from __future__ import annotations

from typing import Any


async def handle_call_subordinate(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    return {"status": "not_configured", "message": "Subordinate agents coming in Phase 2."}
