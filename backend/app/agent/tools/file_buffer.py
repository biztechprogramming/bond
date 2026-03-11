"""Server-side file buffer for large file operations.

Holds files in Python memory so the agent can view windows, search,
and edit without loading entire files into the LLM context.

Key tool: file_smart_edit — compound search + edit in one call.

Inspired by SWE-Agent's file viewer pattern.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections import OrderedDict
from typing import Any

logger = logging.getLogger("bond.agent.tools.file_buffer")

MAX_OPEN_FILES = 10
DEFAULT_WINDOW = 100  # lines
MAX_WINDOW = 300


class FileBuffer:
    """A single file held in memory."""

    def __init__(self, path: str, lines: list[str]):
        self.path = path
        self.lines = lines
        self.last_accessed = time.time()

    @property
    def total_lines(self) -> int:
        return len(self.lines)

    @property
    def size_bytes(self) -> int:
        return sum(len(line) for line in self.lines)

    def view(self, start: int, end: int) -> str:
        """Return lines start..end (1-indexed, inclusive)."""
        start = max(1, start)
        end = min(self.total_lines, end)
        self.last_accessed = time.time()
        # Return with line numbers for easy reference
        result_lines = []
        for i in range(start - 1, end):
            result_lines.append(f"{i + 1:>6}| {self.lines[i]}")
        return "\n".join(result_lines)

    def search(self, pattern: str, context_lines: int = 0, max_matches: int = 50) -> list[dict]:
        """Search for pattern in buffer. Returns matches with line numbers."""
        self.last_accessed = time.time()
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            # Fall back to literal search
            regex = re.compile(re.escape(pattern), re.IGNORECASE)

        matches = []
        for i, line in enumerate(self.lines):
            if regex.search(line):
                line_num = i + 1
                match_entry: dict[str, Any] = {"line": line_num, "text": line.rstrip()}

                if context_lines > 0:
                    ctx_start = max(0, i - context_lines)
                    ctx_end = min(len(self.lines), i + context_lines + 1)
                    context = []
                    for j in range(ctx_start, ctx_end):
                        prefix = ">" if j == i else " "
                        context.append(f"{prefix}{j + 1:>6}| {self.lines[j].rstrip()}")
                    match_entry["context"] = "\n".join(context)

                matches.append(match_entry)
                if len(matches) >= max_matches:
                    break

        return matches

    def replace(self, start: int, end: int, new_content: str) -> dict[str, Any]:
        """Replace lines start..end (1-indexed, inclusive) with new content.

        Writes the result back to disk.
        """
        self.last_accessed = time.time()
        start = max(1, start)
        end = min(self.total_lines, end)

        # Split new content into lines, preserving line endings
        new_lines_normalized = []
        for line in new_content.splitlines(keepends=True):
            new_lines_normalized.append(line)
        # Ensure last line has a newline if the original content did
        if new_lines_normalized and not new_lines_normalized[-1].endswith("\n"):
            # Check if we're at the end of file — last line might not have newline
            if end < self.total_lines:
                new_lines_normalized[-1] += "\n"

        # Replace in buffer
        old_lines = self.lines[start - 1:end]
        self.lines[start - 1:end] = new_lines_normalized

        # Write full file back to disk
        try:
            with open(self.path, "w") as f:
                f.writelines(self.lines)
        except OSError as e:
            # Rollback buffer
            self.lines[start - 1:start - 1 + len(new_lines_normalized)] = old_lines
            return {"error": f"Failed to write file: {e}"}

        return {
            "old_lines": f"{start}-{end} ({end - start + 1} lines)",
            "new_lines": f"{start}-{start + len(new_lines_normalized) - 1} ({len(new_lines_normalized)} lines)",
            "total_lines": self.total_lines,
        }


class FileBufferManager:
    """Manages multiple open file buffers with LRU eviction."""

    def __init__(self, max_files: int = MAX_OPEN_FILES):
        self.max_files = max_files
        self.buffers: OrderedDict[str, FileBuffer] = OrderedDict()

    def _resolve_path(self, path: str) -> str:
        """Resolve to absolute path."""
        if not os.path.isabs(path):
            # Try relative to /workspace first (container), then cwd
            workspace_path = os.path.join("/workspace", path)
            if os.path.exists(workspace_path):
                return os.path.abspath(workspace_path)
        return os.path.abspath(path)

    def open(self, path: str) -> tuple[FileBuffer, bool]:
        """Open a file into the buffer. Returns (buffer, was_already_open).

        Auto-evicts LRU if at capacity.
        """
        abs_path = self._resolve_path(path)

        # Already open — move to end (most recent)
        if abs_path in self.buffers:
            self.buffers.move_to_end(abs_path)
            buf = self.buffers[abs_path]
            buf.last_accessed = time.time()
            return buf, True

        # Read file
        with open(abs_path, "r", errors="replace") as f:
            lines = f.readlines()

        # Evict LRU if at capacity
        while len(self.buffers) >= self.max_files:
            evicted_path, evicted_buf = self.buffers.popitem(last=False)
            logger.info("Evicted file buffer: %s (%d lines)", evicted_path, evicted_buf.total_lines)

        buf = FileBuffer(abs_path, lines)
        self.buffers[abs_path] = buf
        return buf, False

    def get(self, path: str) -> FileBuffer | None:
        """Get an open buffer by path."""
        abs_path = self._resolve_path(path)
        return self.buffers.get(abs_path)

    def get_or_open(self, path: str) -> FileBuffer:
        """Get an existing buffer or open the file."""
        buf = self.get(path)
        if buf is None:
            buf, _ = self.open(path)
        return buf

    def close(self, path: str) -> bool:
        """Close a file buffer."""
        abs_path = self._resolve_path(path)
        if abs_path in self.buffers:
            del self.buffers[abs_path]
            return True
        return False

    def list_open(self) -> list[dict[str, Any]]:
        """List all open file buffers."""
        result = []
        for path, buf in self.buffers.items():
            result.append({
                "path": path,
                "lines": buf.total_lines,
                "size": buf.size_bytes,
            })
        return result


# Global buffer manager instance (one per worker process)
_manager = FileBufferManager()


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


async def handle_file_open(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Open a file into the server-side buffer. Returns summary + first lines.

    The file is held in Python memory — NOT sent to the LLM context.
    Use file_view to see specific sections, file_search to find patterns.
    """
    path = arguments.get("path", "")
    if not path:
        return {"error": "path is required"}

    preview_lines = arguments.get("preview_lines", DEFAULT_WINDOW)
    preview_lines = min(max(1, preview_lines), MAX_WINDOW)

    try:
        buf, was_open = _manager.open(path)
    except FileNotFoundError:
        return {"error": f"File not found: {path}"}
    except OSError as e:
        return {"error": f"Cannot read file: {e}"}

    # Build a summary — enough for the agent to orient itself
    preview = buf.view(1, preview_lines)
    open_files = _manager.list_open()

    return {
        "path": buf.path,
        "total_lines": buf.total_lines,
        "size_bytes": buf.size_bytes,
        "was_already_open": was_open,
        "preview": preview,
        "preview_range": f"1-{min(preview_lines, buf.total_lines)}",
        "open_files": len(open_files),
        "hint": "Use file_view to see other sections, file_search to find patterns, file_replace to edit.",
    }


async def handle_file_view(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """View a window of lines from a buffered file. Auto-opens if needed."""
    path = arguments.get("path", "")
    if not path:
        return {"error": "path is required"}

    start = arguments.get("start_line", 1)
    end = arguments.get("end_line", start + DEFAULT_WINDOW - 1)

    # Cap window size
    if end - start + 1 > MAX_WINDOW:
        end = start + MAX_WINDOW - 1

    try:
        buf = _manager.get_or_open(path)
    except (FileNotFoundError, OSError) as e:
        return {"error": str(e)}

    content = buf.view(start, end)
    actual_start = max(1, start)
    actual_end = min(buf.total_lines, end)

    return {
        "path": buf.path,
        "content": content,
        "range": f"{actual_start}-{actual_end}",
        "total_lines": buf.total_lines,
    }


async def handle_file_search(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Search for a pattern in a buffered file. Auto-opens if needed."""
    path = arguments.get("path", "")
    pattern = arguments.get("pattern", "")
    if not path:
        return {"error": "path is required"}
    if not pattern:
        return {"error": "pattern is required"}

    context_lines = arguments.get("context_lines", 2)
    context_lines = min(max(0, context_lines), 10)
    max_matches = arguments.get("max_matches", 30)

    try:
        buf = _manager.get_or_open(path)
    except (FileNotFoundError, OSError) as e:
        return {"error": str(e)}

    matches = buf.search(pattern, context_lines=context_lines, max_matches=max_matches)

    return {
        "path": buf.path,
        "pattern": pattern,
        "matches": matches,
        "match_count": len(matches),
        "total_lines": buf.total_lines,
    }


async def handle_file_replace(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Replace a range of lines in a buffered file. Writes to disk immediately.

    The agent should use file_search or file_view first to confirm the
    exact lines to replace.
    """
    path = arguments.get("path", "")
    start_line = arguments.get("start_line")
    end_line = arguments.get("end_line")
    new_content = arguments.get("new_content", "")

    if not path:
        return {"error": "path is required"}
    if start_line is None or end_line is None:
        return {"error": "start_line and end_line are required"}

    try:
        buf = _manager.get_or_open(path)
    except (FileNotFoundError, OSError) as e:
        return {"error": str(e)}

    # Show what's being replaced (so the agent can verify in the response)
    old_content = buf.view(start_line, end_line)

    result = buf.replace(start_line, end_line, new_content)
    if "error" in result:
        return result

    return {
        "path": buf.path,
        "replaced": old_content,
        **result,
        "hint": "Use file_view to verify the edit.",
    }


# ---------------------------------------------------------------------------
# file_smart_edit — compound search + preview/edit in one call
# ---------------------------------------------------------------------------


async def handle_file_smart_edit(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Compound tool: find a section of a file and optionally replace it in one call.

    Workflow:
    1. Opens the file into the server-side buffer (if not already open)
    2. Searches for `search` pattern to find the start of the section
    3. If `end_search` is given, scans forward to find the end boundary
       Otherwise uses `lines_after` to determine the selection
    4. If `new_content` is provided: replaces the selection and writes to disk
       If omitted: returns the selection as a preview (dry run)

    This replaces the typical 3-4 call sequence of search → view → edit.
    """
    path = arguments.get("path", "")
    search = arguments.get("search", "")
    end_search = arguments.get("end_search", "")
    lines_before = arguments.get("lines_before", 0)
    lines_after = arguments.get("lines_after", 20)
    occurrence = arguments.get("occurrence", 1)
    new_content = arguments.get("new_content")

    if not path:
        return {"error": "path is required"}
    if not search:
        return {"error": "search is required"}

    # Open/get file
    try:
        buf = _manager.get_or_open(path)
    except (FileNotFoundError, OSError) as e:
        return {"error": str(e)}

    # Compile search pattern
    try:
        start_regex = re.compile(search, re.IGNORECASE)
    except re.error:
        start_regex = re.compile(re.escape(search), re.IGNORECASE)

    # Find the Nth occurrence of start pattern
    match_count = 0
    start_line = None
    for i, line in enumerate(buf.lines):
        if start_regex.search(line):
            match_count += 1
            if match_count == occurrence:
                start_line = i + 1  # 1-indexed
                break

    if start_line is None:
        return {
            "error": f"Pattern '{search}' not found (searched {buf.total_lines} lines, found {match_count} matches, needed occurrence #{occurrence})",
            "path": buf.path,
            "total_lines": buf.total_lines,
        }

    # Determine end of selection
    if end_search:
        # Scan forward from start_line for end pattern
        try:
            end_regex = re.compile(end_search, re.IGNORECASE)
        except re.error:
            end_regex = re.compile(re.escape(end_search), re.IGNORECASE)

        end_line = None
        for i in range(start_line - 1, min(len(buf.lines), start_line - 1 + 500)):
            if i == start_line - 1:
                continue  # skip the start line itself
            if end_regex.search(buf.lines[i]):
                end_line = i + 1  # 1-indexed, inclusive
                break

        if end_line is None:
            # Didn't find end pattern — show what we found and use lines_after
            end_line = min(start_line + lines_after, buf.total_lines)
            return {
                "warning": f"End pattern '{end_search}' not found within 500 lines of start. Showing {lines_after} lines after match instead.",
                "path": buf.path,
                "start_line": max(1, start_line - lines_before),
                "end_line": end_line,
                "content": buf.view(max(1, start_line - lines_before), end_line),
                "total_lines": buf.total_lines,
            }
    else:
        end_line = min(start_line + lines_after, buf.total_lines)

    # Apply lines_before offset
    sel_start = max(1, start_line - lines_before)
    sel_end = end_line

    # Get the selected content
    selected = buf.view(sel_start, sel_end)

    if new_content is None:
        # Preview mode — just return the selection
        return {
            "mode": "preview",
            "path": buf.path,
            "start_line": sel_start,
            "end_line": sel_end,
            "line_count": sel_end - sel_start + 1,
            "content": selected,
            "total_lines": buf.total_lines,
            "hint": "Call again with new_content to apply the edit.",
        }

    # Edit mode — replace and write to disk
    result = buf.replace(sel_start, sel_end, new_content)
    if "error" in result:
        return result

    # Show a snippet of the result for verification
    new_end = sel_start + len(new_content.splitlines()) - 1
    after_edit = buf.view(sel_start, min(new_end + 2, buf.total_lines))

    return {
        "mode": "edited",
        "path": buf.path,
        "replaced": selected,
        "replaced_range": f"{sel_start}-{sel_end}",
        "new_range": f"{sel_start}-{new_end}",
        "after_edit": after_edit,
        **result,
    }
