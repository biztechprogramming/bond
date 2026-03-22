"""Language detection and tree-sitter grammar loading.

Maps file extensions to language names and loads parsers + query files.
"""

from __future__ import annotations

import logging
from importlib import resources
from pathlib import Path
from typing import Any

logger = logging.getLogger("bond.agent.repomap.languages")

# Extension -> language name mapping
EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".lua": "lua",
    ".zig": "zig",
    ".ex": "elixir",
    ".exs": "elixir",
    ".elm": "elm",
    ".hs": "haskell",
    ".dart": "dart",
    ".r": "r",
    ".R": "r",
}

# Cache loaded parsers and languages
_parser_cache: dict[str, Any] = {}
_language_cache: dict[str, Any] = {}


def detect_language(filepath: str) -> str | None:
    """Detect language from file extension. Returns None if unsupported."""
    ext = Path(filepath).suffix.lower()
    return EXTENSION_TO_LANGUAGE.get(ext)


def get_parser(lang: str) -> Any | None:
    """Load and cache a tree-sitter parser for the given language."""
    if lang in _parser_cache:
        return _parser_cache[lang]

    try:
        from tree_sitter_language_pack import get_parser as tslp_get_parser

        parser = tslp_get_parser(lang)
        _parser_cache[lang] = parser
        return parser
    except Exception:
        pass

    # Fallback: try individual tree-sitter-<lang> packages
    try:
        import importlib

        mod = importlib.import_module(f"tree_sitter_{lang}")
        from tree_sitter import Language, Parser

        language = Language(mod.language())
        parser = Parser(language)
        _parser_cache[lang] = parser
        _language_cache[lang] = language
        return parser
    except Exception:
        pass

    _parser_cache[lang] = None
    return None


def get_language(lang: str) -> Any | None:
    """Load and cache a tree-sitter Language object."""
    if lang in _language_cache:
        return _language_cache[lang]

    try:
        from tree_sitter_language_pack import get_language as tslp_get_language

        language = tslp_get_language(lang)
        _language_cache[lang] = language
        return language
    except Exception:
        pass

    # Trigger parser load which also populates language cache
    get_parser(lang)
    return _language_cache.get(lang)


def get_query_scm(lang: str) -> str | None:
    """Load the .scm query file for a language from the queries/ subdirectory.

    Returns the query text, or None if no query file exists.
    """
    queries_dir = Path(__file__).parent / "queries"
    query_file = queries_dir / f"{lang}-tags.scm"
    if query_file.exists():
        return query_file.read_text()
    return None
