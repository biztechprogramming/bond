"""Loader for dynamic tools from the dynamic/ directory.

Scans for Python files (excluding _ prefixed) with SCHEMA and execute().
Registers them into TOOL_MAP and the ToolRegistry.
"""
from __future__ import annotations

import importlib
import inspect
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("bond.agent.tools.dynamic")

_DYNAMIC_DIR = Path(__file__).parent / "dynamic"


def load_dynamic_tool_definitions() -> list[dict]:
    """Scan dynamic/ for tool files and return LiteLLM-compatible tool definitions."""
    definitions = []
    if not _DYNAMIC_DIR.exists():
        return definitions

    for py_file in sorted(_DYNAMIC_DIR.glob("*.py")):
        if py_file.name.startswith("_"):
            continue

        module_name = f"backend.app.agent.tools.dynamic.{py_file.stem}"
        try:
            mod = importlib.import_module(module_name)
        except Exception:
            logger.warning("Failed to import dynamic tool %s", py_file.name, exc_info=True)
            continue

        schema = getattr(mod, "SCHEMA", None)
        execute_fn = getattr(mod, "execute", None)
        if not schema or not execute_fn:
            continue

        # Build LiteLLM-compatible definition
        tool_def = {
            "type": "function",
            "function": {
                "name": schema["name"],
                "description": schema.get("description", ""),
                "parameters": schema.get("parameters", {"type": "object", "properties": {}}),
            },
        }
        definitions.append(tool_def)

    return definitions


def register_dynamic_tools(registry: "ToolRegistry") -> None:  # type: ignore
    """Register all dynamic tools into a ToolRegistry and TOOL_MAP."""
    from .definitions import TOOL_MAP

    if not _DYNAMIC_DIR.exists():
        return

    for py_file in sorted(_DYNAMIC_DIR.glob("*.py")):
        if py_file.name.startswith("_"):
            continue

        module_name = f"backend.app.agent.tools.dynamic.{py_file.stem}"
        try:
            mod = importlib.import_module(module_name)
        except Exception:
            logger.warning("Failed to import dynamic tool %s", py_file.name, exc_info=True)
            continue

        schema = getattr(mod, "SCHEMA", None)
        execute_fn = getattr(mod, "execute", None)
        if not schema or not execute_fn:
            continue

        tool_name = schema["name"]

        # Build LiteLLM tool def for TOOL_MAP
        tool_def = {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": schema.get("description", ""),
                "parameters": schema.get("parameters", {"type": "object", "properties": {}}),
            },
        }
        TOOL_MAP[tool_name] = tool_def

        is_async = inspect.iscoroutinefunction(execute_fn)

        def _create_handler(fn: Any, is_coro: bool):
            async def handler(args: dict, ctx: dict) -> dict:
                try:
                    if is_coro:
                        result = await fn(**args)
                    else:
                        result = fn(**args)
                    if isinstance(result, dict):
                        return result
                    return {"result": result}
                except Exception as e:
                    return {"error": str(e)}
            return handler

        registry.register(tool_name, _create_handler(execute_fn, is_async))
        logger.info("Registered dynamic tool: %s", tool_name)

        # Auto-register keywords for heuristic tool selection
        keywords = schema.get("keywords")
        if keywords and isinstance(keywords, list):
            try:
                from backend.app.agent.tool_selection import TOOL_KEYWORDS, _COMPILED_PATTERNS
                if tool_name not in TOOL_KEYWORDS:
                    TOOL_KEYWORDS[tool_name] = keywords
                    _COMPILED_PATTERNS[tool_name] = [
                        __import__("re").compile(__import__("re").escape(kw), __import__("re").IGNORECASE)
                        for kw in keywords
                    ]
                    logger.info("Registered %d keywords for dynamic tool: %s", len(keywords), tool_name)
            except Exception:
                logger.debug("Could not auto-register keywords for %s", tool_name, exc_info=True)
