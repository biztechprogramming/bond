"""Repo Map — compact indented tree of all tracked files.

Phase 0 of the three-phase agent turn (Design Doc 038).
Uses `git ls-files` to produce a ~3,900 token map for an 800-file repo.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from typing import Set

# Extensions to exclude (binary, generated, locks)
SKIP_EXTS: Set[str] = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot",
    ".map", ".tsbuildinfo", ".lock", ".wasm", ".pyc",
}

# Specific filenames to exclude
SKIP_NAMES: Set[str] = {"package-lock.json", "pnpm-lock.yaml", "bun.lock", "uv.lock"}

# Directories whose individual files are auto-generated — collapse to a summary.
DEFAULT_COLLAPSE_DIRS: Set[str] = {
    "gateway/src/spacetimedb/",
    "frontend/src/lib/spacetimedb/",
}


async def build_repo_map(
    repo_root: str,
    collapse_dirs: Set[str] | None = None,
) -> str:
    """Build a compact indented tree of all tracked files.

    Uses git ls-files (respects .gitignore, ~50ms). Output rules:
    - Directories use indentation for nesting, names end with /
    - Files are bare filenames, one per line
    - Empty files (0 bytes) are dropped
    - Auto-generated directories are collapsed to [generated: N files]
    - No JSON syntax — no braces, quotes, commas, or colons

    Returns: indented tree string (~3,900 tokens for a 800-file repo).
             Empty string if git ls-files fails (not a git repo, etc.).
    """
    if collapse_dirs is None:
        collapse_dirs = DEFAULT_COLLAPSE_DIRS

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

    files = result.stdout.strip().split("\n")

    tree: dict = {}
    for filepath in sorted(files):
        name = os.path.basename(filepath)
        ext = os.path.splitext(name)[1].lower()
        if ext in SKIP_EXTS or name in SKIP_NAMES:
            continue

        full_path = os.path.join(repo_root, filepath)
        try:
            size = os.path.getsize(full_path)
        except OSError:
            continue

        # Drop empty files
        if size == 0:
            continue

        parts = filepath.split("/")
        node = tree
        for part in parts[:-1]:
            key = part + "/"
            node = node.setdefault(key, {})

        node[name] = True  # leaf node — just marks existence

    # Collapse auto-generated directories
    tree = _collapse_generated(tree, collapse_dirs)

    # Render as indented text
    lines = _render_tree(tree)
    return "\n".join(lines)


def _collapse_generated(
    tree: dict,
    collapse_dirs: Set[str],
    path: str = "",
) -> dict:
    """Replace auto-generated directories with a file count summary."""
    result = {}
    for key, value in tree.items():
        full_path = path + key
        if isinstance(value, dict):
            if full_path in collapse_dirs:
                file_count = _count_files(value)
                result[key] = f"[generated: {file_count} files]"
            else:
                collapsed = _collapse_generated(value, collapse_dirs, full_path)
                if collapsed:
                    result[key] = collapsed
        else:
            result[key] = value
    return result


def _count_files(tree: dict) -> int:
    """Recursively count all leaf files in a tree."""
    count = 0
    for value in tree.values():
        if isinstance(value, dict):
            count += _count_files(value)
        else:
            count += 1
    return count


def _render_tree(tree: dict, indent: int = 0) -> list[str]:
    """Render tree as indented lines. Directories show as 'name/', files as 'name'."""
    lines = []
    prefix = " " * indent
    for key, value in tree.items():
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}")
            lines.extend(_render_tree(value, indent + 1))
        elif isinstance(value, str):
            # Collapsed directory summary
            lines.append(f"{prefix}{key} {value}")
        else:
            lines.append(f"{prefix}{key}")
    return lines
