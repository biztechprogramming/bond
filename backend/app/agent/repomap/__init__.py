"""Structural repo map using tree-sitter.

Public API: generate_repo_map()

Orchestrates: file scanning -> tree-sitter parsing -> tag extraction ->
PageRank ranking -> token-budget rendering -> caching.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from collections import defaultdict
from typing import Set

from .cache import RepoMapCache
from .ranking import rank_files
from .tags import Tag, extract_tags

logger = logging.getLogger("bond.agent.repomap")

# Extensions to skip (binary, generated, locks)
SKIP_EXTS: Set[str] = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot",
    ".map", ".tsbuildinfo", ".lock", ".wasm", ".pyc",
    ".min.js", ".min.css",
}

SKIP_NAMES: Set[str] = {"package-lock.json", "pnpm-lock.yaml", "bun.lock", "uv.lock"}

_cache = RepoMapCache()


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: len(text) // 4."""
    return len(text) // 4


def _render_file_full(filepath: str, file_tags: list[Tag]) -> str:
    """Render a file with full signatures for all definitions."""
    defs = [t for t in file_tags if t.kind == "def" and t.signature]
    if not defs:
        return filepath

    # Deduplicate by (name, line)
    seen = set()
    unique_defs = []
    for t in defs:
        key = (t.name, t.line)
        if key not in seen:
            seen.add(key)
            unique_defs.append(t)

    # Sort by line number
    unique_defs.sort(key=lambda t: t.line)

    lines = [filepath]
    for t in unique_defs:
        lines.append(f"\u2502 {t.signature}")
    return "\n".join(lines)


def _render_file_compressed(filepath: str, file_tags: list[Tag]) -> str:
    """Render a file with definition names only (no signatures)."""
    defs = [t for t in file_tags if t.kind == "def"]
    if not defs:
        return filepath

    seen = set()
    unique_names = []
    for t in defs:
        if t.name not in seen:
            seen.add(t.name)
            unique_names.append(t.name)

    lines = [filepath]
    for name in unique_names:
        lines.append(f"\u2502 {name}")
    return "\n".join(lines)


def _render_map(
    tags: list[Tag],
    scores: dict[str, float],
    budget: int,
) -> str:
    """Render the repo map within a token budget.

    Strategy:
    1. Sort files by importance score (descending)
    2. Top-ranked files: full signatures
    3. Mid-ranked files: names only
    4. Low-ranked files: filename only
    5. Drop files that don't fit
    """
    # Group tags by file
    tags_by_file: dict[str, list[Tag]] = defaultdict(list)
    for t in tags:
        tags_by_file[t.rel_fname].append(t)

    # Ensure all files with scores appear (even if no tags)
    all_files = set(scores.keys()) | set(tags_by_file.keys())
    files_ranked = sorted(all_files, key=lambda f: scores.get(f, 0.0), reverse=True)

    sections: list[str] = []
    tokens_used = 0

    for filepath in files_ranked:
        file_tags = tags_by_file.get(filepath, [])

        # Account for separator between sections
        sep_cost = 1 if sections else 0

        # Try full detail first
        full = _render_file_full(filepath, file_tags)
        full_tokens = _estimate_tokens(full) + sep_cost

        if tokens_used + full_tokens <= budget:
            sections.append(full)
            tokens_used += full_tokens
            continue

        # Try compressed (names only)
        compressed = _render_file_compressed(filepath, file_tags)
        comp_tokens = _estimate_tokens(compressed) + sep_cost

        if tokens_used + comp_tokens <= budget:
            sections.append(compressed)
            tokens_used += comp_tokens
            continue

        # Try filename only
        fname_tokens = _estimate_tokens(filepath) + 1 + sep_cost
        if tokens_used + fname_tokens <= budget:
            sections.append(filepath)
            tokens_used += fname_tokens
        else:
            break  # budget exhausted

    return "\n\n".join(sections)


async def generate_repo_map(
    repo_root: str,
    token_budget: int = 10_000,
    focus_files: list[str] | None = None,
    refresh: bool = False,
) -> str:
    """Generate a structural repo map using tree-sitter.

    Args:
        repo_root: Path to the git repository root.
        token_budget: Maximum tokens for the output.
        focus_files: Optional list of file paths to prioritize in the map.
        refresh: Force regeneration, bypassing cache.

    Returns:
        A compact text representation of the repo's structure including
        file paths, class/function signatures, and key relationships.
    """
    t0 = time.monotonic()

    # Get file list via git ls-files
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["git", "ls-files"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""

    if result.returncode != 0 or not result.stdout.strip():
        return ""

    raw_files = result.stdout.strip().split("\n")

    # Filter out binary/generated files
    files: list[str] = []
    for filepath in raw_files:
        name = os.path.basename(filepath)
        ext = os.path.splitext(name)[1].lower()
        if ext in SKIP_EXTS or name in SKIP_NAMES:
            continue
        full_path = os.path.join(repo_root, filepath)
        try:
            if os.path.getsize(full_path) == 0:
                continue
        except OSError:
            continue
        files.append(filepath)

    if not files:
        return ""

    t_files = time.monotonic()
    logger.info("repo-map: file scan done in %.1fs (%d files)", t_files - t0, len(files))

    # Check cache
    if not refresh:
        cached = _cache.get(repo_root, files, token_budget)
        if cached is not None:
            logger.info("repo-map: cache hit in %.1fs", time.monotonic() - t0)
            return cached

    # Extract tags from all files (in a thread to avoid blocking)
    all_tags: list[Tag] = []

    async def _extract_file_tags(filepath: str) -> list[Tag]:
        full_path = os.path.join(repo_root, filepath)
        return await asyncio.to_thread(extract_tags, full_path, filepath)

    # Process files in batches to avoid too many concurrent tasks
    batch_size = 50
    for i in range(0, len(files), batch_size):
        batch = files[i : i + batch_size]
        results = await asyncio.gather(
            *[_extract_file_tags(f) for f in batch],
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, list):
                all_tags.extend(result)

    t_tags = time.monotonic()
    logger.info("repo-map: tag extraction done in %.1fs (%d tags)", t_tags - t_files, len(all_tags))

    # Rank files by importance
    scores = rank_files(all_tags, focus_files=focus_files)

    # Ensure all files appear in scores (even those without tags)
    for f in files:
        if f not in scores:
            scores[f] = 0.0

    t_rank = time.monotonic()
    logger.info("repo-map: ranking done in %.1fs", t_rank - t_tags)

    # Render within token budget
    rendered = _render_map(all_tags, scores, token_budget)

    t_render = time.monotonic()
    logger.info("repo-map: render done in %.1fs", t_render - t_rank)

    # Cache the result
    _cache.set(repo_root, files, token_budget, rendered)

    logger.info("repo-map: total generation %.1fs (files=%d, tags=%d, tokens≈%d)",
                time.monotonic() - t0, len(files), len(all_tags), _estimate_tokens(rendered))

    return rendered
