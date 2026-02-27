"""Heuristic + conversation-aware tool selection.

Reduces the number of tool definitions sent to the primary model each turn
from all enabled tools (~16) to a relevant subset (~4-5), saving ~2,000 tokens/turn.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Always included regardless of heuristics
ALWAYS_INCLUDE = {"respond"}

# Maximum tools to send per turn
MAX_TOOLS_PER_TURN = 8

# Keyword → tool mapping. Keywords are checked case-insensitively against
# the user message and (optionally) the last assistant message.
TOOL_KEYWORDS: dict[str, list[str]] = {
    "file_read": [
        "file", "read", "look at", "show me", "open", "cat ", "source",
        "code in", "check the", "contents of", "what's in", "inspect",
        "review", "examine", ".py", ".ts", ".js", ".md", ".json", ".yaml",
        ".yml", ".toml", ".cfg", ".txt", ".sh", ".sql",
    ],
    "file_write": [
        "write", "create file", "save to", "update file", "edit", "modify",
        "add to file", "change the", "replace", "patch", "refactor",
        "implement", "build", "generate",
    ],
    "code_execute": [
        "run", "execute", "test", "install", "build", "compile", "script",
        "command", "terminal", "shell", "pip", "npm", "make", "docker",
        "git ", "curl", "mkdir", "ls ", "cd ", "grep", "find ",
    ],
    "search_memory": [
        "remember", "recall", "search", "find", "what did", "do you know",
        "previously", "last time", "history", "earlier", "before",
        "mentioned", "told you",
    ],
    "memory_save": [
        "remember this", "save this", "note that", "store", "keep in mind",
        "don't forget", "make a note",
    ],
    "memory_update": [
        "update memory", "correct that", "change what you remember",
        "actually it's", "update what you know",
    ],
    "memory_delete": [
        "forget", "delete memory", "remove that memory", "don't remember",
    ],
    "web_search": [
        "search the web", "google", "look up", "find online", "search for",
        "latest", "current", "news about", "trending",
    ],
    "web_read": [
        "read this url", "fetch", "visit", "browse to", "open url",
        "http://", "https://", "www.", ".com", ".org", ".io",
    ],
    "browser": [
        "browser", "screenshot", "click", "navigate", "webpage", "render",
    ],
    "email": [
        "email", "send mail", "inbox", "message to",
    ],
    "cron": [
        "schedule", "cron", "timer", "recurring", "every hour", "every day",
        "periodically",
    ],
    "notify": [
        "notify", "alert", "ping me", "let me know", "notification",
    ],
    "skills": [
        "skill", "ability", "capability", "plugin",
    ],
    "call_subordinate": [
        "delegate", "subordinate", "sub-agent", "hand off", "ask another",
    ],
}

# Pre-compile patterns for efficiency
_COMPILED_PATTERNS: dict[str, list[re.Pattern]] = {}
for _tool, _keywords in TOOL_KEYWORDS.items():
    _COMPILED_PATTERNS[_tool] = [
        re.compile(re.escape(kw), re.IGNORECASE) for kw in _keywords
    ]


def select_tools(
    user_message: str,
    enabled_tools: list[str],
    recent_tools_used: list[str] | None = None,
    last_assistant_content: str | None = None,
) -> list[str]:
    """Select relevant tools for this turn.

    Args:
        user_message: The current user message
        enabled_tools: All tools enabled for this agent
        recent_tools_used: Tools used in recent turns (for momentum)
        last_assistant_content: Last assistant message (for context)

    Returns:
        List of tool names to include in this turn's API call.
    """
    selected: set[str] = set(ALWAYS_INCLUDE & set(enabled_tools))

    # Text to match against
    match_text = user_message
    if last_assistant_content:
        match_text += " " + last_assistant_content[:500]

    # Keyword matching
    keyword_matched: set[str] = set()
    for tool_name in enabled_tools:
        if tool_name in selected:
            continue
        patterns = _COMPILED_PATTERNS.get(tool_name, [])
        for pattern in patterns:
            if pattern.search(match_text):
                keyword_matched.add(tool_name)
                break

    selected.update(keyword_matched)

    # Momentum: boost tools used in recent turns
    if recent_tools_used:
        # Last 3 unique tools get auto-included
        recent_unique = []
        seen = set()
        for t in reversed(recent_tools_used):
            if t not in seen and t in enabled_tools:
                recent_unique.append(t)
                seen.add(t)
            if len(recent_unique) >= 3:
                break
        selected.update(recent_unique)

    # If nothing matched (generic question), just respond
    if len(selected) <= 1:  # only "respond"
        logger.debug("No tools matched for message, using respond only")
        return [t for t in selected if t in enabled_tools]

    # Coding tasks often need both read + write + execute
    coding_tools = {"file_read", "file_write", "code_execute"}
    if coding_tools & selected:
        # If any coding tool matched, include all enabled coding tools
        selected.update(coding_tools & set(enabled_tools))

    # Memory operations: if any memory tool matched, include search too
    memory_tools = {"search_memory", "memory_save", "memory_update", "memory_delete"}
    if memory_tools & selected:
        if "search_memory" in enabled_tools:
            selected.add("search_memory")

    # Cap at MAX_TOOLS_PER_TURN
    if len(selected) > MAX_TOOLS_PER_TURN:
        # Prioritize: always_include > keyword_matched > momentum
        prioritized = list(ALWAYS_INCLUDE & selected)
        prioritized += [t for t in keyword_matched if t not in prioritized]
        prioritized += [t for t in selected if t not in prioritized]
        selected = set(prioritized[:MAX_TOOLS_PER_TURN])

    result = [t for t in selected if t in enabled_tools]
    logger.info(
        "Tool selection: %d/%d tools selected (keyword=%d, momentum=%d)",
        len(result), len(enabled_tools),
        len(keyword_matched), len(selected) - len(keyword_matched) - len(ALWAYS_INCLUDE & selected),
    )
    return result


def compact_tool_schema(tool_def: dict) -> dict:
    """Create a compact version of a tool schema, stripping verbose descriptions.

    Keeps: function name, first sentence of description, param names/types/enums/required.
    Strips: long descriptions, parameter descriptions, examples.
    """
    func = tool_def.get("function", {})
    desc = func.get("description", "")
    # First sentence only
    short_desc = desc.split(". ")[0].rstrip(".") + "." if desc else ""

    compact: dict[str, Any] = {
        "type": "function",
        "function": {
            "name": func["name"],
            "description": short_desc,
        },
    }

    params = func.get("parameters")
    if params and params.get("properties"):
        compact_props = {}
        for name, prop in params["properties"].items():
            compact_prop: dict[str, Any] = {"type": prop.get("type", "string")}
            if "enum" in prop:
                compact_prop["enum"] = prop["enum"]
            if "items" in prop:
                compact_prop["items"] = {"type": prop["items"].get("type", "string")}
            compact_props[name] = compact_prop

        compact["function"]["parameters"] = {
            "type": "object",
            "properties": compact_props,
            "required": params.get("required", []),
        }

    return compact
