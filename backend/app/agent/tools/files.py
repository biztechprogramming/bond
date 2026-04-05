"""File read/write tools with workspace allowlist enforcement.

When a sandbox image is configured, file operations route through
a persistent helper process in the sandbox container (Phase 2).
If the helper is unavailable, falls back to individual docker exec
calls. Host-mode operations use direct filesystem access with
workspace allowlist enforcement.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from .native import _extract_outline
from .file_buffer import _manager as _file_buffer_manager, track_file_read
from .read_state import (
    get_read_state,
    estimate_tokens,
    truncate_to_tokens,
    MAX_PRE_READ_BYTES,
    MAX_POST_READ_TOKENS,
)

logger = logging.getLogger("bond.agent.tools.files")


def _get_helper_manager():
    """Lazy import to avoid circular dependencies."""
    from backend.app.sandbox.helper_protocol import get_helper_manager
    return get_helper_manager()


def _resolve_and_check(path_str: str, allowed_dirs: list[str]) -> Path | None:
    """Resolve a path and check it falls within an allowed directory.

    Returns the resolved Path if allowed, None if rejected.
    """
    if not allowed_dirs:
        return None

    resolved = Path(os.path.expanduser(path_str)).resolve()
    for allowed in allowed_dirs:
        allowed_resolved = Path(os.path.expanduser(allowed)).resolve()
        try:
            resolved.relative_to(allowed_resolved)
            return resolved
        except ValueError:
            continue
    return None


def _shell_escape(s: str) -> str:
    """Escape a string for safe use in shell commands."""
    return "'" + s.replace("'", "'\\''") + "'"


async def _get_sandbox_container(context: dict[str, Any]) -> str | None:
    """Get or create the sandbox container if sandbox_image is configured."""
    sandbox_image = context.get("sandbox_image")
    if not sandbox_image:
        return None

    from backend.app.sandbox.manager import get_sandbox_manager
    manager = get_sandbox_manager()
    try:
        container_id = await manager.get_or_create_container(
            context.get("agent_id", "default"),
            sandbox_image,
            context.get("workspace_mounts", []),
            agent_name=context.get("agent_name", "agent"),
        )
        return container_id
    except Exception as e:
        logger.warning("Failed to get sandbox container: %s", e)
        return None


async def _multi_read_sandbox(paths: list[str], container_id: str) -> dict:
    """Read multiple files from a sandbox container.

    Tries the persistent helper (batch call) first. Falls back to
    concurrent docker exec calls if the helper is unavailable.
    """
    # Try helper batch read first
    helper_mgr = _get_helper_manager()
    batch_calls = [
        {"method": "file_read", "params": {"path": p}}
        for p in paths
    ]
    batch_results = await helper_mgr.batch(container_id, batch_calls)
    if batch_results is not None:
        results = {}
        for p, resp in zip(paths, batch_results):
            if "error" in resp:
                results[p] = {"error": resp["error"].get("message", str(resp["error"]))}
            elif "result" in resp:
                r = resp["result"]
                results[p] = {
                    "content": r.get("content", ""),
                    "total_lines": r.get("total_lines", 0),
                    "size": r.get("size", 0),
                }
            else:
                results[p] = {"error": "Unexpected helper response"}
        return {"results": results}

    # Fallback: individual docker exec calls
    logger.debug("Helper unavailable, falling back to docker exec for multi-read")

    async def read_one(p: str) -> tuple[str, dict]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", container_id, "cat", p,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode == 0:
                content = stdout.decode("utf-8", errors="replace")
                lines = content.split("\n")
                total_lines = len(lines)
                # Token gate
                token_count = estimate_tokens(content)
                if token_count > MAX_POST_READ_TOKENS:
                    content = truncate_to_tokens(content, MAX_POST_READ_TOKENS)
                    return p, {
                        "content": content, "total_lines": total_lines,
                        "size": len(stdout), "truncated": True,
                    }
                return p, {"content": content, "total_lines": total_lines, "size": len(stdout)}
            return p, {"error": stderr.decode("utf-8", errors="replace").strip()}
        except asyncio.TimeoutError:
            return p, {"error": "Timed out reading file"}

    results = await asyncio.gather(*[read_one(p) for p in paths])
    return {"results": dict(results)}


async def _multi_read_host(paths: list[str], allowed_dirs: list[str]) -> dict:
    """Read multiple files from host filesystem concurrently."""
    rs = get_read_state()

    async def read_one(p: str) -> tuple[str, dict]:
        resolved = _resolve_and_check(p, allowed_dirs)
        if resolved is None:
            return p, {"error": f"Path '{p}' is outside allowed workspace directories."}
        try:
            if not resolved.exists():
                return p, {"error": f"File not found: {p}"}
            if not resolved.is_file():
                return p, {"error": f"Not a file: {p}"}
            track_file_read(str(resolved))

            # mtime dedup
            file_stat = resolved.stat()
            file_mtime = file_stat.st_mtime
            prev = rs.check(str(resolved), file_mtime, None, None)
            if prev is not None:
                return p, {
                    "path": str(resolved),
                    "status": "unchanged",
                    "note": f"File has not changed since last read ({prev.token_count} tokens saved)",
                }

            raw_content = resolved.read_text(encoding="utf-8", errors="replace")
            all_lines = raw_content.splitlines()
            total_lines = len(all_lines)

            # Token gate
            token_count = estimate_tokens(raw_content)
            if token_count > MAX_POST_READ_TOKENS:
                raw_content = truncate_to_tokens(raw_content, MAX_POST_READ_TOKENS)
                rs.record(str(resolved), file_mtime, None, None, estimate_tokens(raw_content))
                return p, {
                    "content": raw_content, "total_lines": total_lines,
                    "size": len(raw_content), "truncated": True,
                }

            rs.record(str(resolved), file_mtime, None, None, token_count)
            return p, {"content": raw_content, "total_lines": total_lines, "size": len(raw_content)}
        except Exception as e:
            return p, {"error": f"Failed to read file: {e}"}

    results = await asyncio.gather(*[read_one(p) for p in paths])
    return {"results": dict(results)}


async def _sandbox_stat(container_id: str, path_str: str) -> tuple[float, int] | None:
    """Get (mtime, size) of a file inside the sandbox container.

    Returns *None* on any failure (file not found, timeout, etc.).
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", container_id, "stat", "-c", "%Y %s", path_str,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode == 0:
            parts = stdout.decode().strip().split()
            return float(parts[0]), int(parts[1])
    except Exception:
        pass
    return None


async def _read_single_sandbox(
    path_str: str,
    container_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Read a single file from sandbox via helper process, with docker exec fallback."""
    line_start = arguments.get("line_start")
    line_end = arguments.get("line_end")
    outline_mode = arguments.get("outline", False)
    rs = get_read_state()

    # Get mtime/size for dedup and byte gate
    stat_info = await _sandbox_stat(container_id, path_str)
    if stat_info is not None:
        file_mtime, file_size = stat_info

        # Phase 3a: mtime dedup
        if not outline_mode:
            prev = rs.check(path_str, file_mtime, line_start, line_end)
            if prev is not None:
                return {
                    "path": path_str,
                    "status": "unchanged",
                    "note": f"File has not changed since last read ({prev.token_count} tokens saved)",
                }

        # Phase 3b: byte gate (skip for line-range and outline modes)
        if not outline_mode and line_start is None and line_end is None:
            if file_size > MAX_PRE_READ_BYTES:
                return {
                    "error": (
                        f"File is {file_size:,} bytes (limit {MAX_PRE_READ_BYTES:,}). "
                        "Use line_start/line_end for specific sections, or outline mode."
                    ),
                    "path": path_str,
                    "size": file_size,
                }

    helper_mgr = _get_helper_manager()

    # Build helper params
    helper_params: dict[str, Any] = {"path": path_str}
    if line_start is not None:
        helper_params["line_start"] = line_start
    if line_end is not None:
        helper_params["line_end"] = line_end

    # Try helper first
    response = await helper_mgr.call(container_id, "file_read", helper_params)
    if response is not None:
        if "error" in response:
            err = response["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            return {"error": msg}
        if "result" in response:
            r = response["result"]
            content = r.get("content", "")

            # Phase 3b: token gate
            token_count = estimate_tokens(content)
            if not outline_mode and token_count > MAX_POST_READ_TOKENS:
                content = truncate_to_tokens(content, MAX_POST_READ_TOKENS)
                if stat_info:
                    rs.record(path_str, stat_info[0], line_start, line_end, estimate_tokens(content))
                return {
                    "content": content,
                    "path": path_str,
                    "size": r.get("size", 0),
                    "total_lines": r.get("total_lines"),
                    "truncated": True,
                    "total_tokens": token_count,
                    "returned_tokens": MAX_POST_READ_TOKENS,
                    "hint": "File exceeds token budget. Use line_start/line_end for specific sections.",
                }

            # Record for future dedup
            if stat_info and not outline_mode:
                rs.record(path_str, stat_info[0], line_start, line_end, token_count)

            result: dict[str, Any] = {
                "content": content,
                "path": path_str,
                "size": r.get("size", 0),
            }
            if "total_lines" in r:
                result["total_lines"] = r["total_lines"]
            if r.get("line_start"):
                result["line_start"] = r["line_start"]
            if r.get("line_end"):
                result["line_end"] = r["line_end"]
            if r.get("truncated"):
                result["truncated"] = True
            return result

    # Fallback: docker exec cat
    logger.debug("Helper unavailable, falling back to docker exec for single read")
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", container_id, "cat", path_str,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
    except asyncio.TimeoutError:
        return {"error": "Timed out reading file"}

    if proc.returncode == 0:
        content = stdout.decode("utf-8", errors="replace")

        # Phase 3b: token gate on fallback path
        token_count = estimate_tokens(content)
        if token_count > MAX_POST_READ_TOKENS:
            content = truncate_to_tokens(content, MAX_POST_READ_TOKENS)
            if stat_info:
                rs.record(path_str, stat_info[0], line_start, line_end, estimate_tokens(content))
            return {
                "content": content,
                "path": path_str,
                "size": len(stdout),
                "truncated": True,
                "total_tokens": token_count,
                "returned_tokens": MAX_POST_READ_TOKENS,
                "hint": "File exceeds token budget. Use line_start/line_end for specific sections.",
            }

        if stat_info:
            rs.record(path_str, stat_info[0], line_start, line_end, token_count)
        return {"content": content, "path": path_str, "size": len(stdout)}
    else:
        return {"error": stderr.decode("utf-8", errors="replace").strip() or "File not found or not readable"}


async def handle_file_read(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Read a file from an allowed workspace directory."""
    # Multi-file mode
    paths_list = arguments.get("paths")
    if paths_list and isinstance(paths_list, list):
        if len(paths_list) > 10:
            return {"error": "Maximum 10 paths allowed in multi-file mode."}
        if arguments.get("line_start") or arguments.get("line_end") or arguments.get("outline"):
            return {"error": "line_start, line_end, and outline are not supported in multi-file mode."}

        container_id = await _get_sandbox_container(context)
        if container_id:
            return await _multi_read_sandbox(paths_list, container_id)

        allowed_dirs = context.get("workspace_dirs", [])
        if not allowed_dirs:
            return {"error": "No workspace directories configured for this agent."}
        return await _multi_read_host(paths_list, allowed_dirs)

    path_str = arguments.get("path", "")

    # Sandbox mode: read via persistent helper or docker exec cat
    container_id = await _get_sandbox_container(context)
    if container_id:
        return await _read_single_sandbox(path_str, container_id, arguments)

    # Host mode: direct filesystem access with allowlist
    allowed_dirs = context.get("workspace_dirs", [])
    if not allowed_dirs:
        return {"error": "No workspace directories configured for this agent."}

    resolved = _resolve_and_check(path_str, allowed_dirs)
    if resolved is None:
        return {"error": f"Path '{path_str}' is outside allowed workspace directories."}

    track_file_read(str(resolved))
    try:
        if not resolved.exists():
            return {"error": f"File not found: {path_str}"}
        if not resolved.is_file():
            return {"error": f"Not a file: {path_str}"}

        line_start = arguments.get("line_start")
        line_end = arguments.get("line_end")
        outline_mode = arguments.get("outline", False)
        file_stat = resolved.stat()
        file_mtime = file_stat.st_mtime
        file_size = file_stat.st_size

        # Phase 3a: mtime dedup — return stub if file unchanged
        rs = get_read_state()
        if not outline_mode:
            prev = rs.check(str(resolved), file_mtime, line_start, line_end)
            if prev is not None:
                return {
                    "path": str(resolved),
                    "status": "unchanged",
                    "note": f"File has not changed since last read ({prev.token_count} tokens saved)",
                    "total_lines": prev.line_end or 0,  # best estimate
                }

        # Phase 3b: byte gate — reject oversized files before reading
        # Skip when line range or outline is requested
        if not outline_mode and line_start is None and line_end is None:
            if file_size > MAX_PRE_READ_BYTES:
                return {
                    "error": (
                        f"File is {file_size:,} bytes (limit {MAX_PRE_READ_BYTES:,}). "
                        "Use line_start/line_end for specific sections, or outline mode."
                    ),
                    "path": str(resolved),
                    "size": file_size,
                }

        raw_content = resolved.read_text(encoding="utf-8", errors="replace")
        all_lines = raw_content.splitlines()
        total_lines = len(all_lines)

        # Outline mode
        if outline_mode:
            outline = _extract_outline(raw_content, resolved.suffix.lower())
            return {
                "path": str(resolved),
                "total_lines": total_lines,
                "size": len(raw_content),
                "outline": outline,
            }

        # Line-range mode
        if line_start is not None or line_end is not None:
            start = (line_start or 1) - 1
            end = line_end if line_end is not None else total_lines

            if start >= total_lines:
                return {"error": f"line_start ({line_start}) exceeds total lines ({total_lines})"}
            if start < 0:
                start = 0
            if end > total_lines:
                end = total_lines

            selected = all_lines[start:end]
            content = "\n".join(selected)

            # Phase 3b: token gate on line-range result
            token_count = estimate_tokens(content)
            if token_count > MAX_POST_READ_TOKENS:
                content = truncate_to_tokens(content, MAX_POST_READ_TOKENS)
                rs.record(str(resolved), file_mtime, line_start, line_end, estimate_tokens(content))
                return {
                    "content": content,
                    "path": str(resolved),
                    "line_start": start + 1,
                    "line_end": end,
                    "total_lines": total_lines,
                    "truncated": True,
                    "total_tokens": token_count,
                    "returned_tokens": MAX_POST_READ_TOKENS,
                    "hint": "File exceeds token budget. Use line_start/line_end for specific sections.",
                }

            rs.record(str(resolved), file_mtime, line_start, line_end, token_count)
            return {
                "content": content,
                "path": str(resolved),
                "line_start": start + 1,
                "line_end": end,
                "total_lines": total_lines,
            }

        # Full file mode — auto-buffer large files
        if total_lines > 500 or len(raw_content) > 100_000:
            logger.info("file_read auto-buffered %s (%d lines)", resolved, total_lines)
            buf = _file_buffer_manager.get_or_open(str(resolved))
            first_lines = buf.view(1, 50)
            last_start = max(1, total_lines - 19)
            last_lines = buf.view(last_start, total_lines)
            outline = _extract_outline(raw_content, resolved.suffix.lower())
            return {
                "path": str(resolved),
                "total_lines": total_lines,
                "size": len(raw_content),
                "first_50_lines": first_lines,
                "last_20_lines": last_lines,
                "outline": outline,
                "hint": (
                    "File auto-buffered (>500 lines). Use line_start/line_end to "
                    "read specific sections, or file_smart_edit for search+edit."
                ),
            }

        # Phase 3b: token gate on full file content
        token_count = estimate_tokens(raw_content)
        if token_count > MAX_POST_READ_TOKENS:
            truncated = truncate_to_tokens(raw_content, MAX_POST_READ_TOKENS)
            rs.record(str(resolved), file_mtime, None, None, estimate_tokens(truncated))
            return {
                "content": truncated,
                "path": str(resolved),
                "total_lines": total_lines,
                "truncated": True,
                "total_tokens": token_count,
                "returned_tokens": MAX_POST_READ_TOKENS,
                "hint": "File exceeds token budget. Use line_start/line_end for specific sections.",
            }

        rs.record(str(resolved), file_mtime, None, None, token_count)
        return {"content": raw_content, "path": str(resolved), "size": file_size, "total_lines": total_lines}
    except Exception as e:
        return {"error": f"Failed to read file: {e}"}


async def handle_file_write(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Write content to a file in an allowed workspace directory."""
    path_str = arguments.get("path", "")
    content = arguments.get("content", "")

    # Sandbox mode: write via docker exec tee with stdin piping
    container_id = await _get_sandbox_container(context)
    if container_id:
        # Ensure parent directory exists
        parent_dir = str(Path(path_str).parent)
        try:
            mkdir_proc = await asyncio.create_subprocess_exec(
                "docker", "exec", container_id, "mkdir", "-p", parent_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(mkdir_proc.communicate(), timeout=10)
        except asyncio.TimeoutError:
            return {"error": "Timed out creating parent directory"}

        # Write content via stdin piping to tee (no heredoc, no escaping needed)
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", "-i", container_id, "tee", path_str,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=content.encode("utf-8")),
                timeout=10,
            )
        except asyncio.TimeoutError:
            return {"error": "Timed out writing file"}

        if proc.returncode == 0:
            get_read_state().invalidate(path_str)
            _file_buffer_manager.close(path_str)
            return {"status": "written", "path": path_str, "bytes": len(content.encode("utf-8"))}
        else:
            return {"error": stderr.decode("utf-8", errors="replace").strip() or "Failed to write file"}

    # Host mode: direct filesystem access with allowlist
    allowed_dirs = context.get("workspace_dirs", [])
    if not allowed_dirs:
        return {"error": "No workspace directories configured for this agent."}

    resolved = _resolve_and_check(path_str, allowed_dirs)
    if resolved is None:
        return {"error": f"Path '{path_str}' is outside allowed workspace directories."}

    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        get_read_state().invalidate(str(resolved))
        _file_buffer_manager.close(str(resolved))
        return {"status": "written", "path": str(resolved), "bytes": len(content.encode("utf-8"))}
    except Exception as e:
        return {"error": f"Failed to write file: {e}"}


async def handle_file_edit(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Apply surgical text replacements to a file (host mode)."""
    path_str = arguments.get("path", "")
    edits = arguments.get("edits", [])
    if not path_str:
        return {"error": "path is required"}
    if not edits or not isinstance(edits, list):
        return {"error": "edits is required and must be a non-empty array"}

    # Sandbox mode: read, apply edits, write back via docker exec
    container_id = await _get_sandbox_container(context)
    if container_id:
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", container_id, "cat", path_str,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        except asyncio.TimeoutError:
            return {"error": "Timed out reading file for edit"}

        if proc.returncode != 0:
            return {"error": stderr.decode("utf-8", errors="replace").strip() or "File not found"}

        content = stdout.decode("utf-8", errors="replace")

        for i, edit in enumerate(edits):
            old_text = edit.get("old_text", "") or edit.get("oldText", "")
            new_text = edit.get("new_text", "") or edit.get("newText", "")
            if not old_text:
                return {"error": f"Edit {i}: old_text is required and must be non-empty"}
            count = content.count(old_text)
            if count == 0:
                return {"error": f"Edit {i}: old_text not found in file"}
            if count > 1:
                return {"error": f"Edit {i}: old_text matches {count} times (ambiguous, must match exactly once)"}
            content = content.replace(old_text, new_text, 1)

        # Write back
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", "-i", container_id, "tee", path_str,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=content.encode("utf-8")),
                timeout=10,
            )
        except asyncio.TimeoutError:
            return {"error": "Timed out writing edited file"}

        if proc.returncode == 0:
            get_read_state().invalidate(path_str)
            _file_buffer_manager.close(path_str)
            return {"status": "edited", "path": path_str, "edits_applied": len(edits)}
        else:
            return {"error": stderr.decode("utf-8", errors="replace").strip() or "Failed to write edited file"}

    # Host mode: direct filesystem access with allowlist
    allowed_dirs = context.get("workspace_dirs", [])
    if not allowed_dirs:
        return {"error": "No workspace directories configured for this agent."}

    resolved = _resolve_and_check(path_str, allowed_dirs)
    if resolved is None:
        return {"error": f"Path '{path_str}' is outside allowed workspace directories."}

    try:
        if not resolved.exists():
            return {"error": f"File not found: {path_str}"}
        if not resolved.is_file():
            return {"error": f"Not a file: {path_str}"}

        content = resolved.read_text(encoding="utf-8", errors="replace")

        for i, edit in enumerate(edits):
            old_text = edit.get("old_text", "") or edit.get("oldText", "")
            new_text = edit.get("new_text", "") or edit.get("newText", "")
            if not old_text:
                return {"error": f"Edit {i}: old_text is required and must be non-empty"}
            count = content.count(old_text)
            if count == 0:
                return {"error": f"Edit {i}: old_text not found in file"}
            if count > 1:
                return {"error": f"Edit {i}: old_text matches {count} times (ambiguous, must match exactly once)"}
            content = content.replace(old_text, new_text, 1)

        resolved.write_text(content, encoding="utf-8")
        get_read_state().invalidate(str(resolved))
        _file_buffer_manager.close(str(resolved))
        return {"status": "edited", "path": str(resolved), "edits_applied": len(edits)}
    except Exception as e:
        return {"error": f"Failed to edit file: {e}"}
