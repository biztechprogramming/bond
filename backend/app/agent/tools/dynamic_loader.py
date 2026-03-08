"""Dynamic prompt hierarchy loader.

Scans the prompts/ directory tree and provides:
- generate_manifest(): compact list of available leaf categories for the system prompt
- load_context_fragments(): loads universal/ + ancestor chain for a given category
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("bond.agent.tools.dynamic_loader")


def generate_manifest(prompts_dir: Path) -> str:
    """Walk the prompts tree and return a compact manifest of leaf categories.

    A leaf node is a file where ``stem == parent.name`` (e.g. ``commits/commits.md``).
    Universal nodes are excluded — they are always loaded implicitly.

    Returns a string like:
        Available context categories:
          engineering.git, engineering.git.commits, ...
    """
    if not prompts_dir.exists():
        return ""

    categories: list[str] = []
    for md_file in sorted(prompts_dir.rglob("*.md")):
        # Only include named fragments (dirname/dirname.md)
        if md_file.stem != md_file.parent.name:
            continue
        try:
            rel = md_file.parent.relative_to(prompts_dir)
        except ValueError:
            continue
        # Skip universal — it's always loaded implicitly
        parts = rel.parts
        if parts and parts[0] == "universal":
            continue
        categories.append(".".join(parts))

    if not categories:
        return ""

    return "Available context categories:\n  " + ", ".join(categories)


def load_context_fragments(category: str, prompts_dir: Path) -> str:
    """Load universal files + one fragment per ancestor level for a category.

    Args:
        category: Dot-separated path, e.g. ``engineering.git.commits``
        prompts_dir: Root of the prompts directory tree.

    Returns:
        Concatenated fragment content separated by ``---``.
        Returns an error message if the category is invalid.
    """
    if not prompts_dir.exists():
        return f"Error: prompts directory not found at {prompts_dir}"

    fragments: list[str] = []

    # Universal fragments are injected into the system prompt at startup — do NOT
    # reload them here. That would duplicate thousands of tokens into conversation
    # history on every load_context call. Only load the specific category chain.

    # Walk the category path, loading one fragment per level
    parts = category.replace(".", "/").split("/")
    parts = [p for p in parts if p]  # filter empty

    if not parts:
        return "\n\n---\n\n".join(fragments) if fragments else "Error: empty category"

    current = prompts_dir
    found_any = False
    for part in parts:
        current = current / part
        fragment_file = current / f"{part}.md"
        if fragment_file.exists():
            try:
                fragments.append(fragment_file.read_text().strip())
                found_any = True
            except Exception as e:
                logger.warning("Failed to read fragment %s: %s", fragment_file, e)

    if not found_any:
        return f"Error: unknown category '{category}'. Check the manifest for available categories."

    return "\n\n---\n\n".join(fragments)


def load_universal_fragments(prompts_dir: Path) -> str:
    """Load all universal fragment files and return them as a single string.

    Called once at system prompt build time so the agent always has these
    guidelines without needing a load_context tool call.
    """
    universal_dir = prompts_dir / "universal"
    if not universal_dir.exists():
        return ""

    fragments: list[str] = []
    for f in sorted(universal_dir.glob("*.md")):
        try:
            fragments.append(f.read_text().strip())
        except Exception as e:
            logger.warning("Failed to read universal fragment %s: %s", f, e)

    return "\n\n---\n\n".join(fragments)


def load_universal_fragments_with_meta(prompts_dir: Path) -> tuple[str, list[dict]]:
    """Load universal fragments and return (content_string, metadata_list).

    Each metadata entry contains source, path, name, and token estimate
    for audit/observability purposes. Content is NOT included in metadata.
    """
    universal_dir = prompts_dir / "universal"
    if not universal_dir.exists():
        return "", []

    fragments: list[str] = []
    meta: list[dict] = []
    for f in sorted(universal_dir.glob("*.md")):
        try:
            content = f.read_text().strip()
            fragments.append(content)
            meta.append({
                "source": "universal",
                "path": str(f.relative_to(prompts_dir)),
                "name": f.stem,
                "tokenEstimate": len(content) // 4,  # rough estimate
            })
        except Exception as e:
            logger.warning("Failed to read universal fragment %s: %s", f, e)

    return "\n\n---\n\n".join(fragments), meta


def load_dynamic_tools(prompts_dir: Path) -> dict:
    """Scan prompts dir for dynamic tool definitions (future extension).

    Currently returns an empty dict. Reserved for tools defined alongside
    prompt fragments.
    """
    return {}
