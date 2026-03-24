"""Workspace Map — discover git repos and build a shallow overview.

Phase 0a of the multi-repo pre-gather flow (Design Doc 069).
Scans the workspace root for git repos (up to 2 levels deep) and produces
a compact directory listing for each.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Set

logger = logging.getLogger("bond.agent.pre_gather")

# Directories to skip when scanning for git repos
SKIP_DIRS: Set[str] = {
    "node_modules", ".venv", "venv", "__pycache__", ".git",
    ".cache", "vendor", "dist", "build", ".tox", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".next", ".nuxt", "target",
    "coverage", ".coverage", "htmlcov", "eggs", "*.egg-info",
}

# Extensions to skip in directory listings
SKIP_EXTS: Set[str] = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot",
    ".map", ".tsbuildinfo", ".lock", ".wasm", ".pyc",
}

SKIP_NAMES: Set[str] = {"package-lock.json", "pnpm-lock.yaml", "bun.lock", "uv.lock"}


@dataclass
class DiscoveredRepo:
    """A git repository discovered in the workspace."""
    name: str              # directory name relative to workspace root
    path: str              # absolute path
    is_git: bool = True


def discover_repos(workspace_root: str, max_depth: int = 2) -> list[DiscoveredRepo]:
    """Scan workspace_root for git repositories up to max_depth levels deep.

    Returns a list of DiscoveredRepo objects, sorted by name.
    Non-git directories at the top level are included with is_git=False.
    """
    repos: list[DiscoveredRepo] = []
    non_git_dirs: list[DiscoveredRepo] = []

    try:
        entries = sorted(os.listdir(workspace_root))
    except OSError:
        return []

    for entry in entries:
        if entry.startswith(".") or entry in SKIP_DIRS:
            continue

        full_path = os.path.join(workspace_root, entry)
        if not os.path.isdir(full_path):
            continue

        # Check if this directory is a git repo
        if os.path.isdir(os.path.join(full_path, ".git")):
            repos.append(DiscoveredRepo(name=entry, path=full_path, is_git=True))
            continue

        # Check one level deeper for git repos
        if max_depth >= 2:
            found_nested = False
            try:
                sub_entries = sorted(os.listdir(full_path))
            except OSError:
                sub_entries = []

            for sub_entry in sub_entries:
                if sub_entry.startswith(".") or sub_entry in SKIP_DIRS:
                    continue
                sub_path = os.path.join(full_path, sub_entry)
                if os.path.isdir(sub_path) and os.path.isdir(os.path.join(sub_path, ".git")):
                    repos.append(DiscoveredRepo(
                        name=f"{entry}/{sub_entry}",
                        path=sub_path,
                        is_git=True,
                    ))
                    found_nested = True

            if not found_nested:
                non_git_dirs.append(DiscoveredRepo(name=entry, path=full_path, is_git=False))
        else:
            non_git_dirs.append(DiscoveredRepo(name=entry, path=full_path, is_git=False))

    # Git repos first, then non-git dirs
    return repos + non_git_dirs


def _should_skip_entry(name: str) -> bool:
    """Check if a file/directory should be skipped in the overview."""
    if name.startswith("."):
        return True
    if name in SKIP_DIRS:
        return True
    ext = os.path.splitext(name)[1].lower()
    if ext in SKIP_EXTS:
        return True
    if name in SKIP_NAMES:
        return True
    return False


def _list_directory(path: str, depth: int, max_depth: int) -> list[str]:
    """Recursively list directory contents up to max_depth.

    Returns indented lines. Directories with many items show [N items] counts
    when at max depth.
    """
    if depth > max_depth:
        return []

    lines: list[str] = []
    indent = "  " * depth

    try:
        entries = sorted(os.listdir(path))
    except OSError:
        return []

    dirs: list[str] = []
    files: list[str] = []

    for entry in entries:
        if _should_skip_entry(entry):
            continue
        full = os.path.join(path, entry)
        if os.path.isdir(full):
            dirs.append(entry)
        elif os.path.isfile(full):
            files.append(entry)

    for d in dirs:
        full = os.path.join(path, d)
        if depth >= max_depth:
            # At max depth, just show item count
            try:
                count = len([e for e in os.listdir(full) if not _should_skip_entry(e)])
            except OSError:
                count = 0
            lines.append(f"{indent}{d}/{'  [' + str(count) + ' items]' if count else ''}")
        else:
            lines.append(f"{indent}{d}/")
            lines.extend(_list_directory(full, depth + 1, max_depth))

    for f in files:
        lines.append(f"{indent}{f}")

    return lines


def build_workspace_overview(workspace_root: str) -> tuple[str, list[DiscoveredRepo]]:
    """Build a shallow workspace overview showing all repos and their structure.

    Returns:
        A tuple of (overview_text, discovered_repos).
        overview_text is a human-readable string showing the workspace structure.
        discovered_repos is the list of DiscoveredRepo objects found.
    """
    repos = discover_repos(workspace_root)

    if not repos:
        return "", []

    sections: list[str] = []

    for repo in repos:
        label = "git" if repo.is_git else "no git"
        header = f"=== {repo.name}/  ({label}) ==="

        # Git repos get 3 levels, non-git get 1 level
        depth_limit = 3 if repo.is_git else 1
        listing = _list_directory(repo.path, depth=0, max_depth=depth_limit)

        section = header + "\n" + "\n".join(listing) if listing else header
        sections.append(section)

    overview = "\n\n".join(sections)

    logger.info(
        "Workspace overview: %d repos (%d git, %d non-git), ~%d tokens",
        len(repos),
        sum(1 for r in repos if r.is_git),
        sum(1 for r in repos if not r.is_git),
        len(overview) // 4,
    )

    return overview, repos
