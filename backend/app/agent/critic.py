"""Lesson candidate management and promotion.

Design Doc 049: Closed-Loop Optimization Engine.

Replaces Doc 048's per-turn critic agent with batch-oriented lesson
generation. Candidates are stored in sqlite-vec (not candidates.jsonl)
and embedded with the shared EmbeddingEngine (Voyage 4, not FastEmbed).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

import asyncio

import aiosqlite
from ulid import ULID

logger = logging.getLogger("bond.agent.critic")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_BOND_DIR = Path("/bond")
if not _BOND_DIR.exists():
    _BOND_DIR = Path(__file__).parent.parent.parent.parent  # repo root

PROMPTS_DIR = _BOND_DIR / "prompts"
OPTIMIZATION_DIR = PROMPTS_DIR / "_optimization"
LESSONS_DIR = OPTIMIZATION_DIR / "lessons"
PROPOSED_DIR = LESSONS_DIR / "proposed"
APPROVED_DIR = LESSONS_DIR / "approved"
REJECTED_DIR = LESSONS_DIR / "rejected"

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

SIMILARITY_THRESHOLD = 0.3
PROMOTION_COUNT = 3


# ---------------------------------------------------------------------------
# Candidate storage (sqlite-vec)
# ---------------------------------------------------------------------------


async def embed_with_fallback(
    text: str,
    engine: Any,  # EmbeddingEngine
    max_retries: int = 2,
) -> list[float] | None:
    """Embed text with retries and exponential backoff.

    Returns the embedding vector, or None on persistent failure.
    """
    for attempt in range(max_retries + 1):
        try:
            return await engine.embed_query(text)
        except Exception:
            if attempt < max_retries:
                wait = 2 ** attempt
                logger.warning(
                    "Embedding attempt %d/%d failed, retrying in %ds",
                    attempt + 1, max_retries + 1, wait,
                )
                await asyncio.sleep(wait)
            else:
                logger.warning("Embedding failed after %d attempts", max_retries + 1)
                return None


async def store_candidate(
    lesson_text: str,
    engine: Any,  # EmbeddingEngine
    db: aiosqlite.Connection,
    source_observation_id: str | None = None,
) -> str | None:
    """Embed a lesson candidate and store in sqlite-vec.

    If recurrence threshold is met, promotes the lesson.
    Returns the candidate ID, or None if the text was empty.
    """
    lesson_text = lesson_text.strip()
    if not lesson_text or lesson_text == "NONE":
        return None

    embedding = await embed_with_fallback(lesson_text, engine)
    candidate_id = str(ULID())

    # Find similar existing candidates (only if embedding succeeded)
    similar_count = 0
    if embedding is not None:
        similar = await find_similar_candidates(embedding, SIMILARITY_THRESHOLD, 20, db)
        similar_count = len(similar)

    # Store the candidate (always, even without embedding)
    await db.execute(
        """
        INSERT INTO optimization_candidates
            (id, lesson_text, source_observation_id, similar_count)
        VALUES (?, ?, ?, ?)
        """,
        (candidate_id, lesson_text, source_observation_id, similar_count),
    )

    # Only store vector if embedding succeeded
    if embedding is not None:
        await db.execute(
            "INSERT INTO optimization_candidates_vec (id, embedding) VALUES (?, ?)",
            (candidate_id, json.dumps(embedding)),
        )
    else:
        logger.warning("Stored candidate %s without embedding vector", candidate_id)

    await db.commit()

    # Check promotion threshold (only meaningful with embedding)
    if embedding is not None:
        hit_count = similar_count + 1
        if hit_count >= PROMOTION_COUNT:
            already = await _already_promoted(lesson_text, embedding, db)
            if not already:
                promote_candidate(lesson_text, hit_count)
                await db.execute(
                    "UPDATE optimization_candidates SET promoted = TRUE, promoted_at = datetime('now') "
                    "WHERE id = ?",
                    (candidate_id,),
                )
                await db.commit()
                logger.info("Promoted lesson (%d hits): %s", hit_count, lesson_text[:100])

    return candidate_id


async def find_similar_candidates(
    embedding: list[float],
    threshold: float = 0.3,
    limit: int = 20,
    db: aiosqlite.Connection | None = None,
) -> list[dict[str, Any]]:
    """Find similar candidates using vec0 cosine search."""
    if db is None:
        return []

    try:
        cursor = await db.execute(
            """
            SELECT c.id, c.lesson_text, c.similar_count, c.created_at,
                   v.distance AS cosine_distance
            FROM optimization_candidates_vec v
            JOIN optimization_candidates c ON c.id = v.id
            WHERE v.embedding MATCH ?
              AND k = ?
            """,
            (json.dumps(embedding), limit),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "lesson_text": r[1],
                "similar_count": r[2],
                "created_at": r[3],
                "similarity": 1.0 - r[4],
            }
            for r in rows
            if (1.0 - r[4]) >= threshold
        ]
    except Exception:
        logger.debug("vec0 similarity search failed", exc_info=True)
        return []


async def _already_promoted(
    lesson_text: str,
    embedding: list[float],
    db: aiosqlite.Connection,
) -> bool:
    """Check if a similar lesson is already in proposed/ or approved/."""
    # Check filesystem
    for directory in (PROPOSED_DIR, APPROVED_DIR):
        if not directory.exists():
            continue
        for f in directory.glob("*.md"):
            content = f.read_text().strip()
            if not content:
                continue
            # Simple text overlap check (cheaper than embedding comparison)
            if _text_overlap(lesson_text, content) > 0.6:
                return True
    return False


def _text_overlap(a: str, b: str) -> float:
    """Quick word-level Jaccard overlap."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


# ---------------------------------------------------------------------------
# Lesson promotion (write .md files)
# ---------------------------------------------------------------------------


def promote_candidate(lesson_text: str, hit_count: int) -> Path:
    """Write a lesson candidate to prompts/_optimization/lessons/proposed/.

    Each lesson gets its own .md file.
    """
    PROPOSED_DIR.mkdir(parents=True, exist_ok=True)

    # Create a slug from the lesson text
    slug = lesson_text[:60].lower()
    slug = "".join(c if c.isalnum() or c == " " else "" for c in slug)
    slug = slug.strip().replace(" ", "-")[:40]
    if not slug:
        slug = hashlib.md5(lesson_text.encode()).hexdigest()[:8]

    filename = f"{date.today()}-{slug}.md"
    filepath = PROPOSED_DIR / filename

    # Avoid overwriting
    if filepath.exists():
        filepath = PROPOSED_DIR / f"{date.today()}-{slug}-{str(ULID())[:6]}.md"

    content = (
        f"# {lesson_text[:80]}\n\n"
        f"{lesson_text}\n\n"
        f"- First observed: {date.today()}\n"
        f"- Recurrences: {hit_count}\n"
    )
    filepath.write_text(content)
    logger.info("Wrote proposed lesson: %s", filepath.name)
    return filepath


# ---------------------------------------------------------------------------
# Lessons injection — called during prompt assembly
# ---------------------------------------------------------------------------


def load_lessons() -> str:
    """Load approved lessons from the filesystem.

    Reads all .md files from prompts/_optimization/lessons/approved/,
    sorted alphabetically. Returns concatenated content or empty string.
    """
    if not APPROVED_DIR.exists():
        return ""

    parts: list[str] = []
    for lesson_file in sorted(APPROVED_DIR.glob("*.md")):
        content = lesson_file.read_text().strip()
        if content:
            parts.append(content)

    if not parts:
        return ""

    return "\n\n## Learned Lessons\n\n" + "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# Lesson generation from observation clusters (uses litellm)
# ---------------------------------------------------------------------------


async def generate_lesson_from_cluster(
    cluster: list[dict[str, Any]],
) -> str | None:
    """Given a cluster of low-scoring observations, generate a lesson candidate.

    Uses litellm.acompletion with a small model for cost efficiency.
    Returns the lesson text or None if generation fails.
    """
    try:
        import litellm

        # Build context from the cluster
        examples = []
        for obs in cluster[:5]:  # Limit to 5 examples
            preview = obs.get("user_message_preview", "")
            score = obs.get("outcome_score", 0.0)
            category = obs.get("task_category", "unknown")
            signals = obs.get("signals_json", "{}")
            if isinstance(signals, str):
                try:
                    signals = json.loads(signals)
                except (json.JSONDecodeError, TypeError):
                    signals = {}
            examples.append(
                f"- Task ({category}, score={score:.2f}): {preview}\n"
                f"  Signals: loop_intervention={signals.get('had_loop_intervention', False)}, "
                f"continuation={signals.get('had_continuation', False)}, "
                f"tool_calls={signals.get('tool_calls', '?')}"
            )

        prompt = (
            "You are analyzing patterns in an AI agent's execution to find improvements.\n\n"
            "These recent turns all scored poorly (< 0.6 out of 1.0) and are semantically similar:\n\n"
            + "\n".join(examples)
            + "\n\n"
            "What single concrete instruction could be added to the agent's system prompt "
            "to prevent this failure pattern? Write ONLY the instruction (one sentence). "
            "If you can't identify a clear pattern, respond with exactly 'NONE'."
        )

        response = await litellm.acompletion(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            temperature=0.3,
        )

        lesson = response.choices[0].message.content.strip()
        if lesson and lesson != "NONE":
            logger.info("Generated lesson from cluster of %d: %s", len(cluster), lesson[:100])
            return lesson
        return None

    except Exception:
        logger.debug("Lesson generation failed", exc_info=True)
        return None
