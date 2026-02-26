"""Native tool handlers for container-side execution.

These replace the host-side handlers when the agent loop runs inside a Docker
container.  File I/O is plain open(), code execution is subprocess, and memory
operations hit a local aiosqlite database instead of the host SQLAlchemy session.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
from ulid import ULID

logger = logging.getLogger("bond.agent.tools.native")

# Max bytes returned by file_read (100 KB)
_MAX_READ_BYTES = 100_000

# Default working directory for code execution (overridable for tests)
_CODE_EXEC_CWD = "/workspace"

# Memory types eligible for promotion to shared memory
_PROMOTABLE_TYPES = frozenset({"preference", "fact", "instruction", "entity", "person"})


# ---------------------------------------------------------------------------
# File tools
# ---------------------------------------------------------------------------

async def handle_file_read(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Read a file from the container filesystem (native open)."""
    path_str = arguments.get("path", "")
    if not path_str:
        return {"error": "path is required"}

    path = Path(path_str)
    if not path.exists():
        return {"error": f"File not found: {path_str}"}
    if not path.is_file():
        return {"error": f"Not a file: {path_str}"}

    try:
        content = path.read_text(errors="replace")
        if len(content) > _MAX_READ_BYTES:
            content = content[:_MAX_READ_BYTES] + "\n\n[Content truncated at 100 KB]"
        return {"content": content, "path": str(path), "size": len(content)}
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

    path = Path(path_str)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(file_content)
        return {"status": "written", "path": str(path), "bytes": len(file_content)}
    except Exception as e:
        return {"error": f"Failed to write {path_str}: {e}"}


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
        return {
            "stdout": stdout.decode(errors="replace"),
            "stderr": stderr.decode(errors="replace"),
            "exit_code": proc.returncode,
        }
    except asyncio.TimeoutError:
        proc.kill()
        return {"stdout": "", "stderr": "Execution timed out", "exit_code": -1}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "exit_code": -1}


# ---------------------------------------------------------------------------
# Memory tools (local aiosqlite)
# ---------------------------------------------------------------------------

async def handle_search_memory(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Search memories using FTS5 on local agent.db + optional shared.db."""
    query = arguments.get("query", "")
    limit = arguments.get("limit", 5)
    agent_db: aiosqlite.Connection | None = context.get("agent_db")

    if agent_db is None:
        return {"results": [], "count": 0, "error": "No agent database available."}

    try:
        results: list[dict] = []

        # Search local memories_fts
        try:
            cursor = await agent_db.execute(
                "SELECT m.id, m.content, m.type, fts.rank "
                "FROM memories_fts fts "
                "JOIN memories m ON m.id = fts.rowid "
                "WHERE memories_fts MATCH ? "
                "ORDER BY fts.rank "
                "LIMIT ?",
                (query, limit * 2),
            )
            rows = await cursor.fetchall()
            for row in rows:
                results.append({
                    "id": row[0],
                    "content": row[1],
                    "type": row[2],
                    "score": abs(row[3]) if row[3] else 0.0,
                    "source": "local",
                })
        except Exception as e:
            logger.debug("Local FTS search failed: %s", e)

        # Search shared.db if attached
        try:
            cursor = await agent_db.execute(
                "SELECT m.id, m.content, m.type, fts.rank "
                "FROM shared.memories_fts fts "
                "JOIN shared.memories m ON m.id = fts.rowid "
                "WHERE shared.memories_fts MATCH ? "
                "ORDER BY fts.rank "
                "LIMIT ?",
                (query, limit * 2),
            )
            rows = await cursor.fetchall()
            for row in rows:
                results.append({
                    "id": row[0],
                    "content": row[1],
                    "type": row[2],
                    "score": abs(row[3]) if row[3] else 0.0,
                    "source": "shared",
                })
        except Exception as e:
            logger.debug("Shared FTS search failed (expected if no shared.db): %s", e)

        # Sort by score descending and limit
        results.sort(key=lambda r: r["score"], reverse=True)
        results = results[:limit]

        return {"results": results, "count": len(results)}
    except Exception as e:
        logger.warning("search_memory failed: %s", e)
        return {"results": [], "count": 0, "error": str(e)}


async def handle_memory_save(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Save a memory to local agent.db. Flag promotable memories for SSE emission."""
    content = arguments.get("content", "")
    memory_type = arguments.get("memory_type", "general")
    summary = arguments.get("summary", content[:100])
    agent_db: aiosqlite.Connection | None = context.get("agent_db")

    if agent_db is None:
        return {"error": "No agent database available."}

    memory_id = str(ULID())
    now = datetime.now(timezone.utc).isoformat()
    promoted = 1 if memory_type in _PROMOTABLE_TYPES else 0

    try:
        await agent_db.execute(
            "INSERT INTO memories "
            "(id, type, content, confidence, promoted, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (memory_id, memory_type, content, 1.0, promoted, now, now),
        )
        # Insert into FTS index
        await agent_db.execute(
            "INSERT INTO memories_fts (rowid, content) VALUES ("
            "(SELECT rowid FROM memories WHERE id = ?), ?)",
            (memory_id, content),
        )
        await agent_db.commit()

        result: dict[str, Any] = {"status": "saved", "memory_id": memory_id}

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
        logger.warning("memory_save failed: %s", e)
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Respond (same as host-side, terminal tool)
# ---------------------------------------------------------------------------

async def handle_respond(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Return the message to the user. Terminal tool \u2014 ends the loop."""
    return {
        "message": arguments.get("message", ""),
        "_terminal": True,
    }
