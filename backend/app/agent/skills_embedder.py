"""Skills embedder — generates embeddings for all indexed skills.

Usage:
    python -m backend.app.agent.skills_embedder [--voyage-key KEY] [--model MODEL] [--batch-size 50]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

from backend.app.agent.tools.skills_db import (
    list_all_skills,
    store_embedding,
)
from backend.app.foundations.embeddings.engine import EmbeddingEngine

logger = logging.getLogger(__name__)

BATCH_SIZE = 50


async def embed_all_skills(
    engine: EmbeddingEngine,
    batch_size: int = BATCH_SIZE,
) -> int:
    """Generate and store embeddings for all skills in skill_index.

    Returns the number of skills embedded.
    """
    skills = await list_all_skills()
    if not skills:
        logger.warning("No skills found in skill_index")
        return 0

    # Build texts to embed: name + description
    texts = []
    skill_ids = []
    for s in skills:
        text = f"{s['name']}: {s.get('description', '')}"
        texts.append(text)
        skill_ids.append(s["id"])

    logger.info("Embedding %d skills in batches of %d", len(texts), batch_size)

    count = 0
    for i in range(0, len(texts), batch_size):
        batch_texts = texts[i : i + batch_size]
        batch_ids = skill_ids[i : i + batch_size]

        try:
            embeddings = await engine.embed(batch_texts)
        except Exception:
            logger.exception("Failed to embed batch %d-%d", i, i + len(batch_texts))
            continue

        for skill_id, embedding in zip(batch_ids, embeddings):
            await store_embedding(skill_id, embedding)
            count += 1

        logger.info("Embedded batch %d-%d (%d/%d)", i, i + len(batch_texts), count, len(texts))

    logger.info("Finished embedding %d skills", count)
    return count


def _build_engine(api_key: str, model: str, dimension: int) -> EmbeddingEngine:
    """Build an EmbeddingEngine with Voyage provider from CLI args."""
    settings = {
        "embedding.api_key.voyage": api_key,
        "embedding.model": model,
        "embedding.output_dimension": str(dimension),
    }
    # db_engine is not used by VoyageAPIProvider, pass None
    return EmbeddingEngine(settings=settings, db_engine=None)  # type: ignore[arg-type]


async def main_async(args: argparse.Namespace) -> None:
    api_key = args.voyage_key or os.environ.get("VOYAGE_API_KEY", "")
    if not api_key:
        logger.error("No Voyage API key provided. Use --voyage-key or set VOYAGE_API_KEY env var.")
        return

    engine = _build_engine(api_key, args.model, args.dimension)
    count = await embed_all_skills(engine, batch_size=args.batch_size)
    print(f"Embedded {count} skills")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="Generate embeddings for indexed skills")
    parser.add_argument("--voyage-key", help="Voyage API key")
    parser.add_argument("--model", default="voyage-4-nano", help="Embedding model name")
    parser.add_argument("--dimension", type=int, default=1024, help="Embedding dimension")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE, help="Batch size for API calls")
    args = parser.parse_args()

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
