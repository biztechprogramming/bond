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
ALWAYS_INCLUDE = {"respond", "load_context"}

# Filesystem toolkit — always included as a group in coding/file contexts.
# ~2,000 tokens total. Cheaper than one wasted iteration.
FILESYSTEM_TOOLKIT = frozenset({
    "file_read", "project_search", "shell_grep", "shell_sed",
    "shell_head", "shell_tail", "shell_awk", "shell_diff",
    "shell_jq", "batch_head", "shell_ls",
})

# Additional shell utilities — included only when keyword-matched.
SHELL_UTILITY_TOOLS = frozenset({
    "git_info", "shell_find", "shell_tree", "shell_wc",
})

# Maximum *non-utility* tools to send per turn
MAX_TOOLS_PER_TURN = 8

# Keyword → tool mapping. Keywords are checked case-insensitively against
# the user message and (optionally) the last assistant message.
TOOL_KEYWORDS: dict[str, list[str]] = {
    "file_read": [
        "file", "read", "look at", "show me", "open", "cat ", "source",
        "code in", "check the", "contents of", "what's in", "inspect",
        "review", "examine", "head ", "tail ", "first lines", "last lines",
        "beginning of", "end of", "top of file", "bottom of file",
        ".py", ".ts", ".js", ".md", ".json", ".yaml",
        ".yml", ".toml", ".cfg", ".txt", ".sh", ".sql",
    ],
    "file_write": [
        "write", "create file", "save to", "update file",
        "add to file", "implement", "build", "generate",
    ],
    "file_edit": [
        "edit", "change", "replace", "fix", "modify", "update",
        "patch", "refactor",
    ],
    "code_execute": [
        "run", "execute", "test", "install", "build", "compile", "script",
        "command", "terminal", "shell", "pip", "npm", "make", "docker",
        "curl",
    ],
    "project_search": [
        "find", "locate", "search for", "where is", "look for", "find the",
        "find me", "looking for", "design doc", "doc ", "document",
        "which file", "what file", "where's the", "can you find",
    ],
    "shell_find": [
        "glob", "find -name", "-type f", "-type d",
    ],
    "shell_ls": [
        "ls ", "list directory", "directory contents",
        "what's in the folder",
    ],
    "shell_grep": [
        "grep", "search for", "find text", "where is", "pattern",
        "look for", "occurrences", "references to",
    ],
    "git_info": [
        "git ", "git status", "git log", "git diff", "branch", "commit history",
        "recent commits", "what changed", "git show",
    ],
    "shell_wc": [
        "count lines", "how many lines", "line count", "word count", "wc ",
    ],
    "shell_head": [
        "head of", "first lines", "beginning of", "top of",
    ],
    "shell_tail": [
        "tail", "last lines", "end of", "bottom of", "log file",
        "recent output", "latest lines",
    ],
    "shell_sed": [
        "sed ", "extract lines", "line range", "lines from",
        "section of", "extract section",
    ],
    "shell_diff": [
        "diff", "compare", "difference between", "what changed",
        "changes between",
    ],
    "shell_awk": [
        "awk ", "columns", "extract column", "csv", "tsv",
        "structured", "tabular",
    ],
    "shell_jq": [
        "jq ", "json query", "parse json", "json file",
        "package.json", "tsconfig",
    ],
    "batch_head": [
        "peek at", "preview files", "batch read", "multiple files",
    ],
    "shell_tree": [
        "tree", "directory structure", "project structure", "folder structure",
        "show structure",
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
    "coding_agent": [
        "coding agent", "sub-agent", "claude code", "codex", "spawn agent",
        "delegate coding", "implement this", "build this feature",
        "multi-file", "refactor the", "complex change", "across multiple files",
        "write the code", "coding task", "have an agent", "let an agent",
    ],
    "work_plan": [
        "implement", "build", "create", "fix", "refactor", "change",
        "update", "migrate", "plan", "task", "work plan", "multi-step",
    ],
    "parallel_orchestrate": [
        "parallel", "concurrent", "simultaneously", "at the same time",
        "in parallel", "batch", "orchestrate", "multiple items",
    ],
    "repo_pr": [
        "pull request", "open pr", "create tool", "propose change",
        "add prompt", "push branch", "new tool", "submit pr",
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
    has_active_plan: bool = False,
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

    # Always include work_plan + parallel_orchestrate if agent has an active plan
    if has_active_plan and "work_plan" in enabled_tools:
        selected.add("work_plan")
    if has_active_plan and "parallel_orchestrate" in enabled_tools:
        selected.add("parallel_orchestrate")

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

    # Coding tasks often need both read + write + execute + coding_agent
    coding_tools = {"file_read", "file_write", "file_edit", "code_execute", "coding_agent"}
    if coding_tools & selected:
        selected.update(coding_tools & set(enabled_tools))

    # Memory operations: if any memory tool matched, include search too
    memory_tools = {"search_memory", "memory_save", "memory_update", "memory_delete"}
    if memory_tools & selected:
        if "search_memory" in enabled_tools:
            selected.add("search_memory")

    # Filesystem toolkit: if ANY file/coding/search tool matched, include
    # the full toolkit (~2,000 tokens). This is cheaper than one wasted
    # iteration where the agent can't read a file it just found.
    filesystem_trigger = (
        coding_tools
        | FILESYSTEM_TOOLKIT
        | SHELL_UTILITY_TOOLS
        | {"project_search", "shell_find", "shell_grep", "shell_ls"}
    )
    if filesystem_trigger & selected:
        selected.update(FILESYSTEM_TOOLKIT & set(enabled_tools))

    # Additional shell utilities only when keyword-matched (not auto-included)
    # git_info, shell_find, shell_tree, shell_wc are niche enough to gate.

    # Separate filesystem tools from regular tools for capping.
    regular_selected = selected - FILESYSTEM_TOOLKIT - SHELL_UTILITY_TOOLS
    fs_selected = selected & (FILESYSTEM_TOOLKIT | SHELL_UTILITY_TOOLS)

    # Cap only non-filesystem tools at MAX_TOOLS_PER_TURN
    if len(regular_selected) > MAX_TOOLS_PER_TURN:
        # Prioritize: always_include > keyword_matched > momentum
        prioritized = list(ALWAYS_INCLUDE & regular_selected)
        prioritized += [t for t in keyword_matched if t not in prioritized and t not in FILESYSTEM_TOOLKIT and t not in SHELL_UTILITY_TOOLS]
        prioritized += [t for t in regular_selected if t not in prioritized]
        regular_selected = set(prioritized[:MAX_TOOLS_PER_TURN])

    selected = regular_selected | fs_selected

    result = [t for t in selected if t in enabled_tools]
    fs_count = len(fs_selected)
    logger.info(
        "Tool selection: %d/%d tools selected (keyword=%d, filesystem=%d, momentum=%d)",
        len(result), len(enabled_tools),
        len(keyword_matched), fs_count,
        len(selected) - len(keyword_matched) - fs_count - len(ALWAYS_INCLUDE & selected),
    )
    return result


# Routing hints appended to compact descriptions so the LLM can differentiate
# similar tools. These survive compact_tool_schema's first-sentence truncation.
TOOL_ROUTING_HINTS: dict[str, str] = {
    "coding_agent": (
        " Use when: multi-file features, refactors, bug fixes requiring "
        "exploration + iteration (10+ tool calls to do yourself). "
        "NOT for: simple edits (use file_edit), reading code (use file_read), "
        "or single commands (use code_execute)."
    ),
    "code_execute": (
        " Use for: running commands (build, test, install, scripts). "
        "NOT for: multi-step coding tasks (use coding_agent)."
    ),
    "file_read": (
        " Reads file content into context. Good for small/medium files (<500 lines)."
        " For large files (>500 lines), prefer shell_grep to find line numbers then"
        " shell_sed to extract just the section you need — avoids loading the whole file."
        " If you have the path, call this directly — never search/find/ls first."
    ),
    "file_edit": (
        " Use for: targeted changes when you know exactly what to write."
    ),
    "host_exec": (
        " Use for: git push, gh CLI, build commands needing host credentials."
    ),
    "call_subordinate": (
        " Reserved for future use. Use coding_agent for coding delegation."
    ),
    "project_search": (
        " ONLY when you don't have the exact path. If you have a path, use file_read directly."
    ),
    "shell_find": (
        " ONLY for glob patterns when you don't have the exact path."
    ),
    "shell_ls": (
        " ONLY to explore an unknown directory. Never to verify a known path."
    ),
    "shell_tail": (
        " Read the end of a file. Great for logs, build output, recent changes."
    ),
    "shell_sed": (
        " Extract line ranges from large files. Use lines='50,100' for lines 50-100."
        " Best tool when you know the line numbers."
    ),
    "shell_diff": (
        " Compare two files. Use to see what changed between versions."
    ),
    "shell_awk": (
        " Structured text extraction. Columns, pattern ranges, CSV/TSV processing."
    ),
    "shell_jq": (
        " Query JSON files. Extract keys, filter arrays, reshape data."
    ),
    "batch_head": (
        " Peek at first N lines of multiple files in one call. Use after project_search."
    ),
    "shell_wc": (
        " ONLY when you specifically need a line/word count, not as a pre-read step."
    ),
    "git_info": (
        " For git status/log/diff/branch/show. NOT as a pre-read verification step."
    ),
}


def compact_tool_schema(tool_def: dict) -> dict:
    """Create a compact version of a tool schema, stripping verbose descriptions.

    Keeps: function name, first sentence of description + routing hint,
    param names/types/enums/required.
    Strips: long descriptions, parameter descriptions, examples.
    """
    func = tool_def.get("function", {})
    desc = func.get("description", "")
    tool_name = func.get("name", "")
    # First sentence only
    short_desc = desc.split(". ")[0].rstrip(".") + "." if desc else ""
    # Append routing hint if available
    hint = TOOL_ROUTING_HINTS.get(tool_name, "")
    if hint:
        short_desc += hint

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
