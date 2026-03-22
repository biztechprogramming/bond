"""Tag extraction from tree-sitter ASTs.

Parses source files and extracts definition/reference tags using .scm query files.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .languages import detect_language, get_language, get_parser, get_query_scm

logger = logging.getLogger("bond.agent.repomap.tags")


@dataclass
class Tag:
    """A named symbol extracted from source code."""

    name: str  # e.g. "agent_turn", "SpacetimeDBClient"
    kind: str  # "def" or "ref"
    rel_fname: str  # relative path from repo root
    fname: str  # absolute path
    line: int  # 0-indexed line number
    signature: str = ""  # full signature line for definitions


def _run_captures(query, node) -> dict:
    """Run tree-sitter query captures, supporting both old and new API."""
    if hasattr(query, "captures"):
        return query.captures(node)

    from tree_sitter import QueryCursor

    cursor = QueryCursor(query)
    return cursor.captures(node)


def extract_tags(fname: str, rel_fname: str, code: str | None = None) -> list[Tag]:
    """Extract definition and reference tags from a source file.

    Args:
        fname: Absolute file path.
        rel_fname: Path relative to repo root.
        code: Optional pre-read source code. If None, reads from fname.

    Returns:
        List of Tag objects for definitions and references found.
    """
    lang = detect_language(fname)
    if not lang:
        return []

    language = get_language(lang)
    parser = get_parser(lang)
    if not language or not parser:
        return []

    query_scm = get_query_scm(lang)
    if not query_scm:
        return []

    if code is None:
        try:
            code = Path(fname).read_text(errors="replace")
        except (OSError, UnicodeDecodeError):
            return []

    if not code:
        return []

    try:
        from tree_sitter import Query

        tree = parser.parse(bytes(code, "utf-8"))
        query = Query(language, query_scm)
        captures = _run_captures(query, tree.root_node)
    except Exception as e:
        logger.debug("Failed to parse %s: %s", fname, e)
        return []

    code_lines = code.splitlines()
    tags = []

    # Process captures: dict of {tag_name: [nodes]}
    all_nodes = []
    if isinstance(captures, dict):
        for tag_name, nodes in captures.items():
            for node in nodes:
                all_nodes.append((node, tag_name))
    else:
        # Old API returns list of (node, tag_name) tuples
        all_nodes = list(captures)

    for node, tag_name in all_nodes:
        if tag_name.startswith("name.definition."):
            kind = "def"
        elif tag_name.startswith("name.reference."):
            kind = "ref"
        else:
            continue

        name = node.text.decode("utf-8") if isinstance(node.text, bytes) else str(node.text)
        line = node.start_point[0]

        # For definitions, extract the signature line
        signature = ""
        if kind == "def" and 0 <= line < len(code_lines):
            sig_line = code_lines[line].strip()
            # Truncate very long lines
            if len(sig_line) > 150:
                sig_line = sig_line[:147] + "..."
            signature = sig_line

        tags.append(
            Tag(
                name=name,
                kind=kind,
                rel_fname=rel_fname,
                fname=fname,
                line=line,
                signature=signature,
            )
        )

    return tags
