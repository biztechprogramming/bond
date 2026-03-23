"""Module-level Langfuse client singleton.

Provides a lazy-initialized Langfuse client for emitting scores and other
non-trace data. The client is only created if LANGFUSE_PUBLIC_KEY is set.

Used by the fragment cost accounting system (Design Doc 064) to emit
per-fragment token and cost scores to Langfuse.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("bond.agent.langfuse_client")

_client: Any | None = None
_initialized: bool = False


def get_langfuse() -> Any | None:
    """Return the module-level Langfuse client, or None if not configured.

    Lazily initializes on first call. Thread-safe enough for our usage
    (worst case: double-init returns same-config client).
    """
    global _client, _initialized

    if _initialized:
        return _client

    _initialized = True

    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        logger.debug("Langfuse client not initialized (LANGFUSE_PUBLIC_KEY not set)")
        return None

    try:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
            secret_key=os.environ.get("LANGFUSE_SECRET_KEY", ""),
            host=os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        )
        logger.info("Langfuse client initialized (host=%s)", _client.base_url)
    except Exception as e:
        logger.warning("Failed to initialize Langfuse client: %s", e)
        _client = None

    return _client


def emit_fragment_scores(
    trace_id: str,
    fragments: list[dict],
    model: str,
    session_id: str,
) -> None:
    """Emit per-fragment token estimate scores to Langfuse.

    Each fragment gets a named score ``fragment_token_est:<name>`` with
    the estimated token count as the value. This makes fragment token
    estimates first-class in Langfuse's analytics — filterable, sortable,
    and aggregatable across traces.

    Args:
        trace_id: The Langfuse trace ID for this turn.
        fragments: List of audit fragment dicts (with 'name', 'tokens'/'tokenEstimate').
        model: The LLM model name.
        session_id: The conversation/session ID.
    """
    lf = get_langfuse()
    if not lf:
        return

    if not os.environ.get("FRAGMENT_COST_SCORES", "true").lower() in ("true", "1", "yes"):
        return

    for frag in fragments:
        frag_name = frag.get("name", "unknown")
        frag_tokens = frag.get("tokens", frag.get("tokenEstimate", 0))
        try:
            lf.score(
                trace_id=trace_id,
                name=f"fragment_token_est:{frag_name}",
                value=frag_tokens,
                comment=frag.get("path", ""),
                metadata={"model": model, "session_id": session_id},
            )
        except Exception as e:
            logger.debug("Failed to emit fragment token score for %s: %s", frag_name, e)


def emit_fragment_cost_scores(
    trace_id: str,
    fragments: list[dict],
    model: str,
    session_id: str,
) -> None:
    """Emit per-fragment USD cost scores to Langfuse.

    Each fragment with a ``usd_cost`` key gets a named score
    ``fragment_cost:<name>`` with the dollar cost as the value.

    Args:
        trace_id: The Langfuse trace ID for this turn.
        fragments: List of enriched fragment dicts (with 'usd_cost' from cost attribution).
        model: The LLM model name.
        session_id: The conversation/session ID.
    """
    lf = get_langfuse()
    if not lf:
        return

    if not os.environ.get("FRAGMENT_COST_SCORES", "true").lower() in ("true", "1", "yes"):
        return

    for frag in fragments:
        frag_name = frag.get("name", "unknown")
        usd_cost = frag.get("usd_cost")
        if usd_cost is None:
            continue
        frag_tokens = frag.get("tokens", frag.get("tokenEstimate", 0))
        try:
            lf.score(
                trace_id=trace_id,
                name=f"fragment_cost:{frag_name}",
                value=usd_cost,
                comment=f"{frag_tokens} tokens @ ${usd_cost:.6f}",
                metadata={"model": model, "session_id": session_id},
            )
        except Exception as e:
            logger.debug("Failed to emit fragment cost score for %s: %s", frag_name, e)


def flush() -> None:
    """Flush the Langfuse client to ensure all scores are sent."""
    if _client:
        try:
            _client.flush()
        except Exception as e:
            logger.debug("Failed to flush Langfuse client: %s", e)
