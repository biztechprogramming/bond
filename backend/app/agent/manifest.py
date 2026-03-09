"""Prompt manifest loader for the three-tier fragment system (Doc 027).

Reads prompts/manifest.yaml and loads prompt file content from disk.
Called once at worker startup, cached in memory.

Tier 1 (always-on): concatenated into system prompt every turn
Tier 2 (lifecycle):  injected when agent enters a specific work phase
Tier 3 (context):    selected by semantic router based on user message
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger("bond.agent.manifest")


@dataclass
class FragmentMeta:
    """Metadata and content for a single prompt fragment."""

    path: str
    tier: int
    phase: Optional[str] = None  # Tier 2 only
    utterances: list[str] = field(default_factory=list)  # Tier 3 only
    content: str = ""  # Loaded from disk
    token_estimate: int = 0  # Rough estimate (len // 4)


# Module-level cache — loaded once, reused across turns.
_manifest_cache: dict[str, FragmentMeta] | None = None
_manifest_mtime: float = 0.0


def load_manifest(prompts_dir: Path, *, force: bool = False) -> dict[str, FragmentMeta]:
    """Load the prompt manifest and read all referenced files from disk.

    Results are cached. Pass force=True to reload after file changes.
    Returns a dict keyed by relative path (e.g. 'universal/safety.md').
    """
    global _manifest_cache, _manifest_mtime

    manifest_path = prompts_dir / "manifest.yaml"
    if not manifest_path.exists():
        logger.warning("Manifest not found at %s — no fragments will be loaded", manifest_path)
        _manifest_cache = {}
        return _manifest_cache

    # Hot-reload if manifest file changed on disk
    try:
        current_mtime = manifest_path.stat().st_mtime
    except OSError:
        current_mtime = 0.0

    if not force and _manifest_cache is not None and current_mtime == _manifest_mtime:
        return _manifest_cache

    raw = yaml.safe_load(manifest_path.read_text())
    if not raw or not isinstance(raw, dict):
        logger.warning("Manifest at %s is empty or invalid", manifest_path)
        _manifest_cache = {}
        return _manifest_cache

    result: dict[str, FragmentMeta] = {}
    for rel_path, meta in raw.items():
        if meta is None:
            continue

        full_path = prompts_dir / rel_path
        if not full_path.exists():
            logger.warning("Manifest references %s but file not found — skipping", rel_path)
            continue

        try:
            content = full_path.read_text().strip()
        except Exception as e:
            logger.warning("Failed to read %s: %s — skipping", rel_path, e)
            continue

        result[rel_path] = FragmentMeta(
            path=rel_path,
            tier=meta.get("tier", 3),
            phase=meta.get("phase"),
            utterances=meta.get("utterances", []),
            content=content,
            token_estimate=len(content) // 4,
        )

    _manifest_cache = result
    _manifest_mtime = current_mtime
    logger.info(
        "Loaded manifest: %d fragments (T1=%d, T2=%d, T3=%d)",
        len(result),
        sum(1 for f in result.values() if f.tier == 1),
        sum(1 for f in result.values() if f.tier == 2),
        sum(1 for f in result.values() if f.tier == 3),
    )
    return result


def get_tier1_fragments(manifest: dict[str, FragmentMeta]) -> list[FragmentMeta]:
    """Return all Tier 1 (always-on) fragments, sorted by path for deterministic ordering."""
    return sorted(
        (f for f in manifest.values() if f.tier == 1),
        key=lambda f: f.path,
    )


def get_tier1_content(manifest: dict[str, FragmentMeta]) -> str:
    """Return Tier 1 fragments concatenated as a single string for the system prompt."""
    fragments = get_tier1_fragments(manifest)
    return "\n\n---\n\n".join(f.content for f in fragments)


def get_tier1_meta(manifest: dict[str, FragmentMeta]) -> list[dict]:
    """Return Tier 1 fragment metadata for audit/observability (no content)."""
    return [
        {
            "source": "manifest-tier1",
            "path": f.path,
            "name": Path(f.path).stem,
            "tokenEstimate": f.token_estimate,
        }
        for f in get_tier1_fragments(manifest)
    ]


def get_tier2_fragments(
    manifest: dict[str, FragmentMeta], phase: str
) -> list[FragmentMeta]:
    """Return Tier 2 fragments for a given lifecycle phase."""
    return sorted(
        (f for f in manifest.values() if f.tier == 2 and f.phase == phase),
        key=lambda f: f.path,
    )


def get_tier3_fragments(manifest: dict[str, FragmentMeta]) -> list[FragmentMeta]:
    """Return all Tier 3 (context-dependent) fragments."""
    return sorted(
        (f for f in manifest.values() if f.tier == 3),
        key=lambda f: f.path,
    )


def invalidate_cache() -> None:
    """Force cache invalidation — useful for testing."""
    global _manifest_cache, _manifest_mtime
    _manifest_cache = None
    _manifest_mtime = 0.0
