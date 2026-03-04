"""Native tool handlers for container-side execution.

These replace the host-side handlers when the agent loop runs inside a Docker
container.  File I/O is plain open(), code execution is subprocess, and memory
operations hit a local aiosqlite database instead of the host SQLAlchemy session.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
from ulid import ULID

logger = logging.getLogger("bond.agent.tools.native")

# Max bytes returned by file_read (10 KB — keeps tool results lean for context window)
_MAX_READ_BYTES = 10_000

# Default working directory for code execution (overridable for tests)
_CODE_EXEC_CWD = "/workspace"

# Workspace root — file_read/file_edit resolve relative paths here first,
# matching code_execute which also runs from /workspace.
_FILE_TOOL_ROOTS = [Path("/workspace"), Path("/bond")]


def _resolve_path(path_str: str) -> Path:
    """Resolve a path for file tools.

    Tries in order:
    1. Absolute path — use as-is.
    2. Relative path against /workspace (matches code_execute cwd).
    3. Relative path against /bond (bond repo root).
    4. Relative path against process cwd (fallback for tests / local dev).
    5. Returns the /workspace-relative path for the error message.
    """
    p = Path(path_str)
    if p.is_absolute():
        return p
    # Try each workspace root
    for root in _FILE_TOOL_ROOTS:
        candidate = root / p
        if candidate.exists():
            return candidate
    # Process cwd fallback (covers tests, local dev outside containers)
    cwd_candidate = Path.cwd() / p
    if cwd_candidate.exists():
        return cwd_candidate
    # Default to workspace-relative for the error message
    return Path(_FILE_TOOL_ROOTS[0]) / p

# Memory types eligible for promotion to shared memory
_PROMOTABLE_TYPES = frozenset({"preference", "fact", "instruction", "entity", "person"})

# Allowed memory types for validation
_ALLOWED_MEMORY_TYPES = frozenset({
    "general", "fact", "solution", "instruction", "preference",
    "entity", "person", "event", "project",
})

# Allowed sensitivity values
_ALLOWED_SENSITIVITY = frozenset({"normal", "personal", "secret"})

# Recency half-life in days (matches host-side search.py)
_RECENCY_HALF_LIFE_DAYS = 30


# ---------------------------------------------------------------------------
# File tools
# ---------------------------------------------------------------------------

_OUTLINE_PATTERNS: dict[str, re.Pattern] = {
    ".py": re.compile(r"^(class |def |async def )", re.MULTILINE),
    ".ts": re.compile(r"^(export )?(async )?(function |class |interface |type |const \w+ =)", re.MULTILINE),
    ".tsx": re.compile(r"^(export )?(async )?(function |class |interface |type |const \w+ =)", re.MULTILINE),
    ".js": re.compile(r"^(export )?(async )?(function |class |const \w+ =)", re.MULTILINE),
    ".jsx": re.compile(r"^(export )?(async )?(function |class |const \w+ =)", re.MULTILINE),
    ".go": re.compile(r"^(func |type )", re.MULTILINE),
    ".rs": re.compile(r"^(pub )?(fn |struct |enum |impl |trait |mod )", re.MULTILINE),
    ".java": re.compile(r"^(public |private |protected )?(static )?(class |interface |void |int |String |boolean |long |double |float |\w+ )", re.MULTILINE),
    ".rb": re.compile(r"^(class |module |def )", re.MULTILINE),
}


def _extract_outline(content: str, suffix: str) -> list[str]:
    """Extract structural outline from file content."""
    pattern = _OUTLINE_PATTERNS.get(suffix)
    lines = content.splitlines()
    if pattern:
        result = []
        for i, line in enumerate(lines, 1):
            if pattern.match(line.lstrip()):
                result.append(f"{i}: {line.strip()}")
        return result
    else:
        # Non-code files: first 5 + last 5 lines
        if len(lines) <= 10:
            return [f"{i}: {line}" for i, line in enumerate(lines, 1)]
        head = [f"{i}: {line}" for i, line in enumerate(lines[:5], 1)]
        tail = [f"{len(lines) - 4 + i}: {line}" for i, line in enumerate(lines[-5:])]
        return head + [f"... ({len(lines) - 10} lines omitted) ..."] + tail


async def handle_file_read(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Read one or more files from the container filesystem (native open)."""
    path_str = arguments.get("path")
    paths_list = arguments.get("paths")

    # Handle multiple paths in parallel
    if paths_list and isinstance(paths_list, list):
        tasks = []
        for p in paths_list:
            if not isinstance(p, str):
                continue
            # Create a localized arguments dict for each path
            local_args = arguments.copy()
            del local_args["paths"]
            local_args["path"] = p
            tasks.append(handle_file_read(local_args, context))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        combined_results = []
        for res in results:
            if isinstance(res, Exception):
                combined_results.append({"error": str(res)})
            else:
                combined_results.append(res)
        return {"results": combined_results}

    if not path_str:
        return {"error": "path or paths is required"}

    path = _resolve_path(path_str)
    if not path.exists():
        return {"error": f"File not found: {path_str} (looked in /workspace and /bond)"}
    if not path.is_file():
        return {"error": f"Not a file: {path_str}"}

    try:
        raw_content = path.read_text(errors="replace")
        all_lines = raw_content.splitlines()
        total_lines = len(all_lines)

        # Outline mode
        if arguments.get("outline"):
            outline = _extract_outline(raw_content, path.suffix.lower())
            return {
                "path": str(path),
                "total_lines": total_lines,
                "size": len(raw_content),
                "outline": outline,
            }

        # Line-range mode
        line_start = arguments.get("line_start")
        line_end = arguments.get("line_end")

        if line_start is not None or line_end is not None:
            start = (line_start or 1) - 1  # convert to 0-indexed
            end = line_end if line_end is not None else total_lines

            if start >= total_lines:
                return {"error": f"line_start ({line_start}) exceeds total lines ({total_lines})"}
            if start < 0:
                start = 0
            if end > total_lines:
                end = total_lines

            selected = all_lines[start:end]
            content = "\n".join(selected)
            return {
                "content": content,
                "path": str(path),
                "line_start": start + 1,
                "line_end": end,
                "total_lines": total_lines,
            }

        # Full file mode
        content = raw_content
        if len(content) > _MAX_READ_BYTES:
            content = content[:_MAX_READ_BYTES] + f"\n\n[Content truncated at {_MAX_READ_BYTES // 1000} KB — use line_start/line_end to read specific sections]"
        return {"content": content, "path": str(path), "size": len(content), "total_lines": total_lines}
    except Exception as e:
        return {"error": f"Failed to read {path_str}: {e}"}


async def handle_file_write(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Write a file to the container filesystem (native open)."""
    path_str = arguments.get("path", "")
    file_content = arguments.get("content", "")
    if not path_str:
        return {"error": "path is required"}

    # Resolve relative paths the same way file_read/file_edit do
    path = _resolve_path(path_str)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(file_content)
        return {"status": "written", "path": str(path), "bytes": len(file_content)}
    except Exception as e:
        return {"error": f"Failed to write {path_str}: {e}"}


async def handle_file_edit(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Apply surgical text replacements to a file."""
    path_str = arguments.get("path", "")
    edits = arguments.get("edits", [])
    if not path_str:
        return {"error": "path is required"}
    if not edits or not isinstance(edits, list):
        return {"error": "edits is required and must be a non-empty array"}

    path = _resolve_path(path_str)
    if not path.exists():
        return {"error": f"File not found: {path_str} (looked in /workspace and /bond)"}
    if not path.is_file():
        return {"error": f"Not a file: {path_str}"}

    try:
        content = path.read_text(errors="replace")

        for i, edit in enumerate(edits):
            old_text = edit.get("old_text", "")
            new_text = edit.get("new_text", "")
            if not old_text:
                return {"error": f"Edit {i}: old_text is required and must be non-empty"}

            count = content.count(old_text)
            if count == 0:
                return {"error": f"Edit {i}: old_text not found in file"}
            if count > 1:
                return {"error": f"Edit {i}: old_text matches {count} times (ambiguous, must match exactly once)"}

            content = content.replace(old_text, new_text, 1)

        path.write_text(content)
        return {"status": "edited", "path": str(path), "edits_applied": len(edits)}
    except Exception as e:
        return {"error": f"Failed to edit {path_str}: {e}"}


# ---------------------------------------------------------------------------
# Code execution
# ---------------------------------------------------------------------------

async def handle_code_execute(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Execute code via subprocess inside the container."""
    language = arguments.get("language", "python")
    code = arguments.get("code", "")
    timeout = arguments.get("timeout", 30)

    if language == "python":
        cmd = ["python3", "-c", code]
    elif language == "shell":
        cmd = ["sh", "-c", code]
    else:
        return {"error": f"Unsupported language: {language}", "exit_code": -1}

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=_CODE_EXEC_CWD,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout,
        )
        stdout_str = stdout.decode(errors="replace")
        stderr_str = stderr.decode(errors="replace")
        # Truncate large outputs to keep context lean
        if len(stdout_str) > _MAX_READ_BYTES:
            stdout_str = stdout_str[:_MAX_READ_BYTES] + f"\n[stdout truncated at {_MAX_READ_BYTES // 1000} KB]"
        if len(stderr_str) > _MAX_READ_BYTES:
            stderr_str = stderr_str[:_MAX_READ_BYTES] + f"\n[stderr truncated at {_MAX_READ_BYTES // 1000} KB]"
        return {
            "stdout": stdout_str,
            "stderr": stderr_str,
            "exit_code": proc.returncode,
        }
    except asyncio.TimeoutError:
        proc.kill()
        return {"stdout": "", "stderr": "Execution timed out", "exit_code": -1}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "exit_code": -1}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _recency_boost(created_at: str | None) -> float:
    """Compute a small recency boost based on age.

    Uses exponential decay with a 30-day half-life.
    Returns a value between 0.0 and 0.01 (small enough not to dominate ranking).
    """
    if not created_at:
        return 0.0
    try:
        if isinstance(created_at, str):
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        else:
            return 0.0
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_days = max(0, (now - dt).total_seconds() / 86400)
        decay = math.exp(-0.693 * age_days / _RECENCY_HALF_LIFE_DAYS)
        return 0.01 * decay
    except (ValueError, TypeError):
        return 0.0


def _build_fts_where(
    arguments: dict[str, Any],
    prefix: str = "m",
) -> tuple[str, list[Any]]:
    """Build optional WHERE clauses for memory search filters.

    Returns (extra_sql, params) to append after the FTS MATCH clause.
    """
    clauses: list[str] = []
    params: list[Any] = []

    # Exclude soft-deleted
    clauses.append(f"{prefix}.deleted_at IS NULL")

    # Type filtering
    memory_types = arguments.get("memory_types")
    if memory_types and isinstance(memory_types, list):
        placeholders = ", ".join("?" for _ in memory_types)
        clauses.append(f"{prefix}.type IN ({placeholders})")
        params.extend(memory_types)

    # Time filtering
    since = arguments.get("since")
    if since and isinstance(since, str):
        clauses.append(f"{prefix}.created_at >= ?")
        params.append(since)

    until = arguments.get("until")
    if until and isinstance(until, str):
        clauses.append(f"{prefix}.created_at <= ?")
        params.append(until)

    return " AND " + " AND ".join(clauses), params


# ---------------------------------------------------------------------------
# Memory tools (local aiosqlite)
# ---------------------------------------------------------------------------

async def handle_search_memory(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Search memories using FTS5 on local agent.db + optional shared.db."""
    t0 = time.monotonic()
    query = arguments.get("query", "")
    limit = arguments.get("limit", 5)
    agent_db: aiosqlite.Connection | None = context.get("agent_db")

    # Validation
    if not query or not isinstance(query, str) or not query.strip():
        return {"error": "query is required and must be non-empty"}
    if not isinstance(limit, int) or limit <= 0:
        return {"error": "limit must be a positive integer"}
    memory_types = arguments.get("memory_types")
    if memory_types is not None and (
        not isinstance(memory_types, list)
        or not all(isinstance(t, str) for t in memory_types)
    ):
        return {"error": "memory_types must be a list of strings"}

    if agent_db is None:
        return {"results": [], "count": 0, "error": "No agent database available."}

    try:
        results: list[dict] = []

        # Build filter clauses
        extra_where, extra_params = _build_fts_where(arguments, prefix="m")

        # Search local memories_fts
        try:
            sql = (
                "SELECT m.id, m.content, m.type, m.summary, m.created_at, fts.rank "
                "FROM memories_fts fts "
                "JOIN memories m ON m.id = fts.id "
                "WHERE memories_fts MATCH ? " + extra_where + " "
                "ORDER BY fts.rank "
                "LIMIT ?"
            )
            cursor = await agent_db.execute(
                sql,
                [query] + extra_params + [limit * 2],
            )
            rows = await cursor.fetchall()
            for row in rows:
                score = abs(row[5]) if row[5] else 0.0
                boost = _recency_boost(row[4])
                results.append({
                    "id": row[0],
                    "content": row[1],
                    "type": row[2],
                    "summary": row[3],
                    "score": score + boost,
                    "source": "local",
                })
        except Exception as e:
            logger.debug("Local FTS search failed: %s", e)

        # Search shared.db if attached (read-only, skip access tracking)
        try:
            # Shared DB may have different schema; use simpler filter (no deleted_at check
            # because shared.db is host-managed and already excludes deleted)
            shared_extra = ""
            shared_params: list[Any] = []
            memory_types = arguments.get("memory_types")
            if memory_types and isinstance(memory_types, list):
                placeholders = ", ".join("?" for _ in memory_types)
                shared_extra = f" AND m.type IN ({placeholders})"
                shared_params.extend(memory_types)

            # NOTE: FTS5 MATCH uses table alias (fts) not schema-qualified name
            # because SQLite doesn't support schema prefix in FTS MATCH clause
            sql = (
                "SELECT m.id, m.content, m.type, m.summary, m.created_at, memories_fts.rank "
                "FROM shared.memories_fts "
                "JOIN shared.memories m ON m.id = memories_fts.id "
                "WHERE memories_fts MATCH ?" + shared_extra + " "
                "ORDER BY memories_fts.rank "
                "LIMIT ?"
            )
            cursor = await agent_db.execute(
                sql,
                [query] + shared_params + [limit * 2],
            )
            rows = await cursor.fetchall()
            for row in rows:
                score = abs(row[5]) if row[5] else 0.0
                boost = _recency_boost(row[4])
                results.append({
                    "id": row[0],
                    "content": row[1],
                    "type": row[2],
                    "summary": row[3],
                    "score": score + boost,
                    "source": "shared",
                })
        except Exception as e:
            logger.debug("Shared FTS search failed (expected if no shared.db): %s", e)

        # Sort by score descending and limit
        results.sort(key=lambda r: r["score"], reverse=True)
        results = results[:limit]

        # Access tracking for local results
        local_ids = [r["id"] for r in results if r["source"] == "local"]
        if local_ids:
            now = datetime.now(timezone.utc).isoformat()
            for mid in local_ids:
                try:
                    await agent_db.execute(
                        "UPDATE memories SET access_count = access_count + 1, "
                        "last_accessed_at = ? WHERE id = ?",
                        (now, mid),
                    )
                except Exception:
                    pass  # non-critical
            await agent_db.commit()

        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
        local_count = sum(1 for r in results if r["source"] == "local")
        shared_count = sum(1 for r in results if r["source"] == "shared")
        logger.info(
            "search_memory query=%r results=%d (local=%d shared=%d) elapsed=%.1fms",
            query, len(results), local_count, shared_count, elapsed_ms,
        )

        return {"results": results, "count": len(results)}
    except Exception as e:
        logger.warning("search_memory failed: %s", e, exc_info=True)
        return {"results": [], "count": 0, "error": str(e)}


async def handle_memory_save(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Save a memory to local agent.db. Flag promotable memories for SSE emission."""
    content = arguments.get("content", "")
    memory_type = arguments.get("memory_type", "general")
    agent_db: aiosqlite.Connection | None = context.get("agent_db")

    # Validation
    if not content or not isinstance(content, str) or not content.strip():
        return {"error": "content is required and must be non-empty"}
    if memory_type not in _ALLOWED_MEMORY_TYPES:
        return {"error": f"Invalid memory_type '{memory_type}'. Allowed: {sorted(_ALLOWED_MEMORY_TYPES)}"}

    sensitivity = arguments.get("sensitivity", "normal")
    if sensitivity not in _ALLOWED_SENSITIVITY:
        return {"error": f"Invalid sensitivity '{sensitivity}'. Allowed: {sorted(_ALLOWED_SENSITIVITY)}"}

    importance = arguments.get("importance", 0.5)
    if not isinstance(importance, (int, float)) or importance < 0.0 or importance > 1.0:
        return {"error": "importance must be a number between 0.0 and 1.0"}

    if agent_db is None:
        return {"error": "No agent database available."}

    summary = arguments.get("summary", content[:100])
    metadata = arguments.get("metadata", "{}")
    if isinstance(metadata, dict):
        import json
        metadata = json.dumps(metadata)
    source_type = arguments.get("source_type")
    source_id = arguments.get("source_id")

    memory_id = str(ULID())
    version_id = str(ULID())
    now = datetime.now(timezone.utc).isoformat()
    promoted = 1 if memory_type in _PROMOTABLE_TYPES else 0

    try:
        # Insert memory
        await agent_db.execute(
            "INSERT INTO memories "
            "(id, type, content, summary, source_type, source_id, "
            "sensitivity, metadata, importance, access_count, "
            "confidence, promoted, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1.0, ?, ?, ?)",
            (memory_id, memory_type, content, summary, source_type, source_id,
             sensitivity, metadata, importance, promoted, now, now),
        )

        # Insert version 1
        await agent_db.execute(
            "INSERT INTO memory_versions "
            "(id, memory_id, version, previous_content, new_content, "
            "previous_type, new_type, changed_by, change_reason, created_at) "
            "VALUES (?, ?, 1, NULL, ?, NULL, ?, 'system', 'initial creation', ?)",
            (version_id, memory_id, content, memory_type, now),
        )

        await agent_db.commit()

        result: dict[str, Any] = {"status": "saved", "memory_id": memory_id}

        logger.info(
            "memory_save id=%s type=%s content_len=%d promoted=%s",
            memory_id, memory_type, len(content), bool(promoted),
        )

        # If promotable, attach _promote dict for SSE emission
        if promoted:
            result["_promote"] = {
                "type": memory_type,
                "content": content,
                "summary": summary,
                "source_memory_id": memory_id,
            }

        return result
    except Exception as e:
        await agent_db.rollback()
        logger.warning("memory_save failed: %s", e, exc_info=True)
        return {"error": str(e)}


async def handle_memory_update(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Update an existing memory's content with version tracking."""
    memory_id = arguments.get("memory_id", "")
    content = arguments.get("content", "")
    reason = arguments.get("reason", "")
    agent_db: aiosqlite.Connection | None = context.get("agent_db")

    # Validation
    if not memory_id or not isinstance(memory_id, str) or not memory_id.strip():
        return {"error": "memory_id is required and must be non-empty"}
    if not content or not isinstance(content, str) or not content.strip():
        return {"error": "content is required and must be non-empty"}

    if agent_db is None:
        return {"error": "No agent database available."}

    try:
        # Fetch current memory (must exist and not be deleted)
        cursor = await agent_db.execute(
            "SELECT id, content, type FROM memories "
            "WHERE id = ? AND deleted_at IS NULL",
            (memory_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return {"error": f"Memory not found: {memory_id}"}

        prev_content = row[1]
        mem_type = row[2]

        # Get current max version
        cursor = await agent_db.execute(
            "SELECT MAX(version) FROM memory_versions WHERE memory_id = ?",
            (memory_id,),
        )
        ver_row = await cursor.fetchone()
        next_version = (ver_row[0] or 0) + 1

        # Update memory content + summary (summary auto-derived from content)
        now = datetime.now(timezone.utc).isoformat()
        summary = arguments.get("summary", content[:100])
        await agent_db.execute(
            "UPDATE memories SET content = ?, summary = ?, updated_at = ? WHERE id = ?",
            (content, summary, now, memory_id),
        )

        # Insert version record
        version_id = str(ULID())
        await agent_db.execute(
            "INSERT INTO memory_versions "
            "(id, memory_id, version, previous_content, new_content, "
            "previous_type, new_type, changed_by, change_reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'agent', ?, ?)",
            (version_id, memory_id, next_version, prev_content, content,
             mem_type, mem_type, reason, now),
        )

        await agent_db.commit()

        logger.info(
            "memory_update id=%s version=%d",
            memory_id, next_version,
        )

        return {"status": "updated", "memory_id": memory_id, "version": next_version}
    except Exception as e:
        await agent_db.rollback()
        logger.warning("memory_update failed: %s", e, exc_info=True)
        return {"error": str(e)}


async def handle_memory_delete(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Soft-delete a memory by setting deleted_at."""
    memory_id = arguments.get("memory_id", "")
    reason = arguments.get("reason", "deleted by agent")
    agent_db: aiosqlite.Connection | None = context.get("agent_db")

    # Validation
    if not memory_id or not isinstance(memory_id, str) or not memory_id.strip():
        return {"error": "memory_id is required and must be non-empty"}

    if agent_db is None:
        return {"error": "No agent database available."}

    try:
        now = datetime.now(timezone.utc).isoformat()

        # Soft delete
        cursor = await agent_db.execute(
            "UPDATE memories SET deleted_at = ? "
            "WHERE id = ? AND deleted_at IS NULL",
            (now, memory_id),
        )
        if cursor.rowcount == 0:
            return {"error": "Memory not found or already deleted"}

        # Get current content for version record
        cursor = await agent_db.execute(
            "SELECT content, type FROM memories WHERE id = ?",
            (memory_id,),
        )
        row = await cursor.fetchone()
        prev_content = row[0] if row else ""
        mem_type = row[1] if row else ""

        # Get next version number
        cursor = await agent_db.execute(
            "SELECT MAX(version) FROM memory_versions WHERE memory_id = ?",
            (memory_id,),
        )
        ver_row = await cursor.fetchone()
        next_version = (ver_row[0] or 0) + 1

        # Record deletion in versions
        version_id = str(ULID())
        await agent_db.execute(
            "INSERT INTO memory_versions "
            "(id, memory_id, version, previous_content, new_content, "
            "previous_type, new_type, changed_by, change_reason, created_at) "
            "VALUES (?, ?, ?, ?, '[deleted]', ?, ?, 'agent', ?, ?)",
            (version_id, memory_id, next_version, prev_content,
             mem_type, mem_type, reason, now),
        )

        await agent_db.commit()

        logger.info("memory_delete id=%s", memory_id)

        return {"status": "deleted", "memory_id": memory_id}
    except Exception as e:
        await agent_db.rollback()
        logger.warning("memory_delete failed: %s", e, exc_info=True)
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Respond (same as host-side, terminal tool)
# ---------------------------------------------------------------------------

async def handle_respond(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Return the message to the user. Terminal tool -- ends the loop."""
    return {
        "message": arguments.get("message", ""),
        "_terminal": True,
    }


# ---------------------------------------------------------------------------
# Repo PR — propose changes to the Bond repo
# ---------------------------------------------------------------------------

async def handle_repo_pr(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Create a branch, write files, commit, push, and open a PR."""
    import subprocess
    import os

    branch = arguments.get("branch", "")
    title = arguments.get("title", "")
    body = arguments.get("body", "")
    files = arguments.get("files", {})
    commit_message = arguments.get("commit_message", "")

    if not branch or not title or not files or not commit_message:
        return {"error": "branch, title, files, and commit_message are all required"}

    bond_root = Path("/bond")
    if not (bond_root / ".git").exists():
        return {"error": "/bond is not a git repository. This agent does not have repo write access."}

    try:
        # Return to main, pull latest
        subprocess.run(["git", "checkout", "main"], cwd=bond_root, check=True, capture_output=True)
        subprocess.run(["git", "pull", "origin", "main", "--ff-only"], cwd=bond_root, capture_output=True)

        # Create branch
        result = subprocess.run(["git", "checkout", "-b", branch], cwd=bond_root, capture_output=True)
        if result.returncode != 0:
            # Branch may already exist
            subprocess.run(["git", "checkout", branch], cwd=bond_root, check=True, capture_output=True)

        # Write files
        for rel_path, content in files.items():
            target = bond_root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        # Stage and commit
        subprocess.run(["git", "add", "-A"], cwd=bond_root, check=True, capture_output=True)
        diff_result = subprocess.run(["git", "diff", "--cached", "--stat"], cwd=bond_root, capture_output=True)
        if not diff_result.stdout.strip():
            return {"status": "no_changes", "message": "Nothing to commit — files are identical to current branch."}

        subprocess.run(["git", "commit", "-m", commit_message], cwd=bond_root, check=True, capture_output=True)

        # Push
        push_result = subprocess.run(
            ["git", "push", "-u", "origin", branch],
            cwd=bond_root, capture_output=True,
        )
        if push_result.returncode != 0:
            stderr = push_result.stderr.decode()
            return {"error": f"Push failed: {stderr[:300]}"}

        # Create PR via GitHub API
        github_token = os.getenv("GITHUB_TOKEN", "")
        if not github_token:
            return {"status": "pushed", "message": f"Branch {branch!r} pushed. Set GITHUB_TOKEN to auto-create PRs."}

        import httpx
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.github.com/repos/biztechprogramming/bond/pulls",
                headers={
                    "Authorization": f"Bearer {github_token}",
                    "Accept": "application/vnd.github+json",
                },
                json={"title": title, "body": body, "head": branch, "base": "main"},
            )
            if resp.status_code == 201:
                pr_url = resp.json().get("html_url", "")
                return {"status": "pr_created", "pr_url": pr_url}
            elif resp.status_code == 422:
                return {"status": "pushed", "message": f"Branch pushed. PR may already exist. ({resp.text[:200]})"}
            else:
                return {"status": "pushed", "message": f"Branch pushed but PR creation failed ({resp.status_code}): {resp.text[:200]}"}

    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if e.stderr else ""
        return {"error": f"Git error: {stderr or str(e)}"}
    finally:
        subprocess.run(["git", "checkout", "main"], cwd=bond_root, capture_output=True)


# ---------------------------------------------------------------------------
# Load Context (prompt hierarchy)
# ---------------------------------------------------------------------------

async def handle_load_context(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Load prompt context fragments for a given category."""
    category = arguments.get("category", "")
    if not category:
        return {"error": "category is required"}

    from backend.app.agent.tools.dynamic_loader import load_context_fragments

    prompts_dir = Path(__file__).parent.parent.parent.parent.parent / "prompts"
    # Fallback: container path
    if not prompts_dir.exists():
        prompts_dir = Path("/bond/prompts")

    result = load_context_fragments(category, prompts_dir)
    if result.startswith("Error:"):
        return {"error": result}
    return {"context": result}


# ---------------------------------------------------------------------------
# Parallel Orchestration
# ---------------------------------------------------------------------------

async def handle_parallel_orchestrate(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Execute multiple tool calls in parallel batches."""
    from backend.app.agent.tools.native_registry import build_native_registry

    plan_data = arguments.get("plan", {})
    batches = plan_data.get("batches", [])
    if not batches:
        return {"error": "No batches found in parallel plan."}

    registry = build_native_registry()
    batch_results = []

    for i, batch in enumerate(batches):
        batch_name = batch.get("batch_name", f"Batch {i+1}")
        calls = batch.get("calls", [])
        
        logger.info("Executing parallel batch: %s (%d calls)", batch_name, len(calls))
        
        tasks = []
        for call in calls:
            tool_name = call.get("tool_name")
            args = call.get("arguments", {})
            
            # TODO: Integrate model_override once litellm support is added to registry
            if tool_name:
                tasks.append(registry.execute(tool_name, args, context))

        # Run all calls in the batch concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        processed_results = []
        for res in results:
            if isinstance(res, Exception):
                processed_results.append({"error": str(res)})
            else:
                processed_results.append(res)
        
        batch_results.append({
            "batch_name": batch_name,
            "results": processed_results
        })

    return {
        "status": "completed",
        "batches_executed": len(batches),
        "results": batch_results
    }
