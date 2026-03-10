"""Semantic router for Tier 3 fragment selection (Doc 027 Phase 3).

Uses FastEmbed (ONNX Runtime) embeddings to match user messages against
manifest utterances. Fast local inference, no API calls.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from backend.app.agent.manifest import FragmentMeta, get_tier3_fragments, load_manifest

logger = logging.getLogger("bond.agent.fragment_router")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ENCODER_MODEL = "BAAI/bge-small-en-v1.5"
SCORE_THRESHOLD = 0.4  # Minimum similarity to include a route
LOW_CONFIDENCE_THRESHOLD = 0.6  # Warn when best score is below this

# ---------------------------------------------------------------------------
# Module-level cache
# ---------------------------------------------------------------------------

_router: Optional[Any] = None  # semantic_router.SemanticRouter
_route_to_fragment: dict[str, FragmentMeta] = {}
_encoder: Optional[Any] = None


def build_route_layer(prompts_dir: Path) -> None:
    """Build (or return cached) SemanticRouter from Tier 3 manifest entries.

    Called once at startup / first turn. Subsequent calls are no-ops
    unless rebuild_routes() has been called.
    """
    global _router

    if _router is not None:
        return

    _build_routes(prompts_dir)


def rebuild_routes(prompts_dir: Path) -> None:
    """Force-rebuild the route layer (e.g. after manifest hot-reload)."""
    global _router, _route_to_fragment, _encoder
    _router = None
    _route_to_fragment = {}
    _encoder = None
    _build_routes(prompts_dir)


def _build_routes(prompts_dir: Path) -> None:
    """Internal: construct SemanticRouter from manifest Tier 3 entries."""
    global _router, _route_to_fragment, _encoder

    from semantic_router import Route, SemanticRouter
    from semantic_router.encoders import FastEmbedEncoder

    manifest = load_manifest(prompts_dir)
    tier3 = get_tier3_fragments(manifest)

    encoder = FastEmbedEncoder(name=ENCODER_MODEL)
    _encoder = encoder

    if not tier3:
        logger.warning("No Tier 3 fragments found — semantic router will be empty")
        _router = SemanticRouter(encoder=encoder, routes=[], auto_sync="local")
        _route_to_fragment = {}
        return

    routes: list[Route] = []
    route_map: dict[str, FragmentMeta] = {}

    for frag in tier3:
        if not frag.utterances:
            logger.debug("Skipping %s — no utterances defined", frag.path)
            continue
        route_name = frag.path
        routes.append(Route(
            name=route_name,
            utterances=frag.utterances,
            score_threshold=SCORE_THRESHOLD,
        ))
        route_map[route_name] = frag

    if not routes:
        logger.warning("All Tier 3 fragments had empty utterances — router will be empty")

    _router = SemanticRouter(encoder=encoder, routes=routes, auto_sync="local")
    _route_to_fragment = route_map

    logger.info(
        "Built semantic route layer: %d routes from %d Tier 3 fragments",
        len(routes),
        len(tier3),
    )


async def select_fragments_by_similarity(
    user_message: str, top_k: int = 5
) -> list[FragmentMeta]:
    """Select Tier 3 fragments by semantic similarity to user message.

    Uses the router's index to query all route embeddings, groups scores
    by route name, and returns the top_k routes above SCORE_THRESHOLD.
    """
    if _router is None or _encoder is None:
        logger.warning("Route layer not initialized — call build_route_layer() first")
        return []

    if not user_message or not user_message.strip():
        return []

    if not _route_to_fragment:
        return []

    try:
        # Encode the query and search the index for all matching embeddings
        import numpy as np
        xq = np.array(_encoder([user_message]))
        scores, route_names = _router.index.query(vector=xq[0], top_k=len(_route_to_fragment) * 3)
    except Exception as e:
        logger.error("Semantic route lookup failed: %s", e)
        return []

    if scores is None or len(scores) == 0:
        return []

    # Group by route name, take max score per route
    best_scores: dict[str, float] = {}
    for score, name in zip(scores, route_names):
        name = str(name)
        if name in _route_to_fragment:
            score_val = float(score)
            if score_val >= SCORE_THRESHOLD:
                if name not in best_scores or score_val > best_scores[name]:
                    best_scores[name] = score_val

    if not best_scores:
        return []

    # Sort by score descending, take top_k
    ranked = sorted(best_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

    max_score = ranked[0][1] if ranked else 0.0
    if max_score < LOW_CONFIDENCE_THRESHOLD:
        logger.warning(
            "Low confidence in fragment selection (max=%.2f < %.2f) for: %.80s",
            max_score,
            LOW_CONFIDENCE_THRESHOLD,
            user_message,
        )

    fragments = [_route_to_fragment[name] for name, _ in ranked]

    logger.info(
        "Tier 3 selection: %d fragments (scores: %s) for: %.80s",
        len(fragments),
        ", ".join(f"{name}={score:.2f}" for name, score in ranked),
        user_message,
    )

    return fragments


def get_tier3_meta(fragments: list[FragmentMeta]) -> list[dict]:
    """Return Tier 3 fragment metadata for audit/observability (no content)."""
    return [
        {
            "source": "semantic-router-tier3",
            "path": f.path,
            "name": Path(f.path).stem,
            "tokenEstimate": f.token_estimate,
        }
        for f in fragments
    ]
