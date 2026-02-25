"""Cron scheduling tool — stub for Phase 3."""

from __future__ import annotations

from typing import Any


async def handle_cron(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    return {"status": "not_configured", "message": "Cron scheduling coming in Phase 3."}
