#!/usr/bin/env python3
"""Bond helper process — runs inside the sandbox container.

Accepts JSON-RPC requests on stdin, returns JSON responses on stdout.
This avoids the overhead of spawning a new `docker exec` for every tool call.

Protocol:
    Request:  {"id": 1, "method": "file_read", "params": {"path": "/workspace/foo.py"}}
    Response: {"id": 1, "result": {"content": "...", "total_lines": 42, "mtime": 1712170000.0}}

    Batch:    {"id": 2, "method": "batch", "params": {"calls": [...]}}
    Response: {"id": 2, "result": [{"result": ...}, {"result": ...}]}

    Error:    {"id": 3, "error": {"code": -1, "message": "File not found"}}

Reads from stdin line-by-line, writes to stdout line-by-line.
Stderr is reserved for debug logging (not part of the protocol).
"""

from __future__ import annotations

import json
import os
import sys
import traceback


def handle_file_read(params: dict) -> dict:
    """Read a file, optionally with line range."""
    path = params.get("path", "")
    if not path:
        return {"error": {"code": -1, "message": "Missing 'path' parameter"}}

    try:
        stat = os.stat(path)
        mtime = stat.st_mtime
        size = stat.st_size
    except FileNotFoundError:
        return {"error": {"code": -1, "message": f"File not found: {path}"}}
    except OSError as e:
        return {"error": {"code": -1, "message": f"Cannot stat file: {e}"}}

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except IsADirectoryError:
        return {"error": {"code": -1, "message": f"Not a file: {path}"}}
    except OSError as e:
        return {"error": {"code": -1, "message": f"Failed to read: {e}"}}

    all_lines = raw.splitlines()
    total_lines = len(all_lines)

    line_start = params.get("line_start")
    line_end = params.get("line_end")

    if line_start is not None or line_end is not None:
        start = (line_start or 1) - 1
        end = line_end if line_end is not None else total_lines
        start = max(0, min(start, total_lines))
        end = max(start, min(end, total_lines))
        selected = all_lines[start:end]
        content = "\n".join(selected)
        return {
            "result": {
                "content": content,
                "line_start": start + 1,
                "line_end": end,
                "total_lines": total_lines,
                "mtime": mtime,
                "size": size,
            }
        }

    # Full file — truncate if too large
    content = raw
    truncated = False
    if len(raw) > 100_000:
        content = raw[:100_000] + "\n... [truncated at 100KB]"
        truncated = True

    return {
        "result": {
            "content": content,
            "total_lines": total_lines,
            "mtime": mtime,
            "size": size,
            "truncated": truncated,
        }
    }


def handle_file_stat(params: dict) -> dict:
    """Stat a file — returns mtime, size, exists."""
    path = params.get("path", "")
    if not path:
        return {"error": {"code": -1, "message": "Missing 'path' parameter"}}
    try:
        stat = os.stat(path)
        return {
            "result": {
                "exists": True,
                "mtime": stat.st_mtime,
                "size": stat.st_size,
                "is_file": os.path.isfile(path),
                "is_dir": os.path.isdir(path),
            }
        }
    except FileNotFoundError:
        return {"result": {"exists": False}}
    except OSError as e:
        return {"error": {"code": -1, "message": str(e)}}


def handle_file_write(params: dict) -> dict:
    """Write content to a file."""
    path = params.get("path", "")
    content = params.get("content", "")
    if not path:
        return {"error": {"code": -1, "message": "Missing 'path' parameter"}}
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        stat = os.stat(path)
        return {"result": {"path": path, "size": stat.st_size, "mtime": stat.st_mtime}}
    except OSError as e:
        return {"error": {"code": -1, "message": f"Failed to write: {e}"}}


def handle_grep(params: dict) -> dict:
    """Search for a pattern in files."""
    import subprocess

    pattern = params.get("pattern", "")
    path = params.get("path", ".")
    if not pattern:
        return {"error": {"code": -1, "message": "Missing 'pattern' parameter"}}

    cmd = ["grep", "-rn"]
    if params.get("ignore_case"):
        cmd.append("-i")
    if params.get("include"):
        cmd.extend(["--include", params["include"]])
    max_count = params.get("max_count")
    if max_count:
        cmd.extend(["-m", str(max_count)])
    context_lines = params.get("context_lines")
    if context_lines:
        cmd.extend(["-C", str(context_lines)])
    cmd.extend(["--", pattern, path])

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15
        )
        output = result.stdout
        if len(output) > 10_000:
            output = output[:10_000] + "\n[output truncated at 10KB]"
        return {
            "result": {
                "stdout": output,
                "stderr": result.stderr[:2000] if result.stderr else "",
                "exit_code": result.returncode,
            }
        }
    except subprocess.TimeoutExpired:
        return {"error": {"code": -1, "message": "grep timed out"}}
    except Exception as e:
        return {"error": {"code": -1, "message": str(e)}}


def handle_shell(params: dict) -> dict:
    """Execute a shell command."""
    import subprocess

    command = params.get("command", "")
    if not command:
        return {"error": {"code": -1, "message": "Missing 'command' parameter"}}
    timeout = params.get("timeout", 15)
    cwd = params.get("cwd", "/workspace")

    try:
        result = subprocess.run(
            ["sh", "-c", command],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        stdout = result.stdout
        if len(stdout) > 10_000:
            stdout = stdout[:10_000] + "\n[output truncated at 10KB]"
        return {
            "result": {
                "stdout": stdout,
                "stderr": result.stderr[:2000] if result.stderr else "",
                "exit_code": result.returncode,
            }
        }
    except subprocess.TimeoutExpired:
        return {"error": {"code": -1, "message": "Command timed out"}}
    except Exception as e:
        return {"error": {"code": -1, "message": str(e)}}


def handle_ping(params: dict) -> dict:
    """Health check."""
    return {"result": {"status": "ok", "pid": os.getpid()}}


# Method dispatch table
METHODS: dict[str, callable] = {
    "file_read": handle_file_read,
    "file_stat": handle_file_stat,
    "file_write": handle_file_write,
    "grep": handle_grep,
    "shell": handle_shell,
    "ping": handle_ping,
}


def handle_batch(params: dict) -> dict:
    """Execute multiple calls and return all results."""
    calls = params.get("calls", [])
    if not calls:
        return {"error": {"code": -1, "message": "Empty batch"}}

    results = []
    for call in calls:
        method = call.get("method", "")
        call_params = call.get("params", {})
        handler = METHODS.get(method)
        if handler is None:
            results.append({"error": {"code": -1, "message": f"Unknown method: {method}"}})
        else:
            try:
                results.append(handler(call_params))
            except Exception as e:
                results.append({"error": {"code": -1, "message": str(e)}})
    return {"result": results}


def dispatch(request: dict) -> dict:
    """Dispatch a single JSON-RPC request."""
    req_id = request.get("id")
    method = request.get("method", "")
    params = request.get("params", {})

    if method == "batch":
        response = handle_batch(params)
    else:
        handler = METHODS.get(method)
        if handler is None:
            response = {"error": {"code": -1, "message": f"Unknown method: {method}"}}
        else:
            try:
                response = handler(params)
            except Exception as e:
                response = {"error": {"code": -1, "message": str(e)}}

    if req_id is not None:
        response["id"] = req_id
    return response


def main():
    """Main loop — read JSON lines from stdin, write JSON lines to stdout."""
    # Signal readiness
    sys.stdout.write(json.dumps({"ready": True, "pid": os.getpid()}) + "\n")
    sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            response = {"error": {"code": -2, "message": f"Invalid JSON: {e}"}}
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
            continue

        response = dispatch(request)
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
