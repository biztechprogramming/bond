"""Coding agent tool — spawns a coding sub-agent process.

Design doc: 037-coding-agent-skill.md §4.2

Supports Claude Code, Codex, and Pi. The sub-agent runs as a subprocess
(either on the host or inside the agent container) and streams output back
to the parent agent loop via SSE events.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import time
from pathlib import Path
from typing import Any, AsyncIterator

logger = logging.getLogger("bond.agent.tools.coding_agent")

# ---------------------------------------------------------------------------
# Agent command templates
# ---------------------------------------------------------------------------

AGENT_COMMANDS: dict[str, dict[str, Any]] = {
    "claude": {
        "binary": "claude",
        "args": ["--dangerously-skip-permissions", "--print"],
        "needs_pty": False,
    },
    "codex": {
        "binary": "codex",
        "args": ["exec", "--full-auto"],
        "needs_pty": True,
    },
    "pi": {
        "binary": "pi",
        "args": ["-p"],
        "needs_pty": True,
    },
}

# Required environment variables per agent type
REQUIRED_ENV: dict[str, str] = {
    "claude": "ANTHROPIC_API_KEY",
    "codex": "OPENAI_API_KEY",
    "pi": "ANTHROPIC_API_KEY",
}

# Allowed workspace roots (configurable via BOND_ALLOWED_WORKSPACE_ROOTS)
DEFAULT_ALLOWED_ROOTS = ["/home", "/workspace", "/tmp", "/bond"]


def _get_allowed_roots() -> list[str]:
    """Return list of allowed workspace root paths."""
    env_roots = os.environ.get("BOND_ALLOWED_WORKSPACE_ROOTS")
    if env_roots:
        return [r.strip() for r in env_roots.split(":") if r.strip()]
    return DEFAULT_ALLOWED_ROOTS


def _validate_working_directory(working_dir: str) -> str | None:
    """Validate working directory exists and is under allowed roots.

    Returns an error string if invalid, None if OK.
    """
    path = Path(working_dir).resolve()
    if not path.is_dir():
        return f"Directory not found: {working_dir}"

    allowed = _get_allowed_roots()
    if not any(str(path).startswith(root) for root in allowed):
        return (
            f"Directory {working_dir} is not under any allowed workspace root. "
            f"Allowed roots: {allowed}"
        )
    return None


# ---------------------------------------------------------------------------
# CodingAgentProcess
# ---------------------------------------------------------------------------


class CodingAgentProcess:
    """Manages a coding sub-agent subprocess."""

    def __init__(
        self,
        agent_type: str,
        task: str,
        working_directory: str,
        timeout_minutes: int = 30,
    ):
        self.agent_type = agent_type
        self.task = task
        self.working_directory = working_directory
        self.timeout = timeout_minutes * 60
        self.process: asyncio.subprocess.Process | None = None
        self.output_lines: list[str] = []
        self.start_time: float = 0
        self._killed = False
        self._pty_reader: asyncio.StreamReader | None = None
        self._pty_transport: asyncio.BaseTransport | None = None
        self._master_fd: int | None = None

    async def start(self) -> None:
        """Start the sub-agent process."""
        config = AGENT_COMMANDS.get(self.agent_type)
        if not config:
            raise ValueError(f"Unknown agent type: {self.agent_type}")

        binary = shutil.which(config["binary"])
        if not binary:
            raise FileNotFoundError(
                f"{config['binary']} not found in PATH. "
                f"Install it or choose a different agent_type."
            )

        cmd = [binary] + config["args"] + [self.task]
        env = {**os.environ, "TERM": "dumb", "NO_COLOR": "1"}

        logger.info(
            "Spawning %s in %s (timeout=%ds)",
            self.agent_type,
            self.working_directory,
            self.timeout,
        )

        self.start_time = time.monotonic()

        if config["needs_pty"]:
            await self._start_with_pty(cmd, env)
        else:
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self.working_directory,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )

    async def _start_with_pty(self, cmd: list[str], env: dict[str, str]) -> None:
        """Start process with a PTY (required for Codex/Pi)."""
        import pty as pty_mod

        master_fd, slave_fd = pty_mod.openpty()
        self._master_fd = master_fd

        # Override TERM for PTY agents
        env["TERM"] = "xterm-256color"

        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.working_directory,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=env,
        )
        os.close(slave_fd)

        # Set up async reader for PTY master
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        loop = asyncio.get_event_loop()
        transport, _ = await loop.connect_read_pipe(
            lambda: protocol, os.fdopen(master_fd, "rb", closefd=False)
        )
        self._pty_reader = reader
        self._pty_transport = transport

    async def stream_output(self) -> AsyncIterator[str]:
        """Yield lines as the sub-agent produces them."""
        reader = self._pty_reader or (
            self.process.stdout if self.process else None
        )
        if not reader:
            return

        try:
            async for raw_line in reader:
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                self.output_lines.append(line)
                yield line
        except (ConnectionError, asyncio.IncompleteReadError):
            # Process ended, PTY closed
            pass

    async def wait(self) -> int:
        """Wait for process with timeout. Returns exit code."""
        if not self.process:
            return -1
        try:
            return await asyncio.wait_for(
                self.process.wait(), timeout=self.timeout
            )
        except asyncio.TimeoutError:
            logger.warning("Coding agent timed out after %ds", self.timeout)
            await self.kill()
            return -1

    async def kill(self) -> None:
        """Kill the sub-agent process."""
        if self.process and not self._killed:
            self._killed = True
            try:
                self.process.send_signal(signal.SIGTERM)
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
            except ProcessLookupError:
                pass
            finally:
                self._cleanup_pty()

    def _cleanup_pty(self) -> None:
        """Clean up PTY resources."""
        if self._pty_transport:
            self._pty_transport.close()
            self._pty_transport = None
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.start_time if self.start_time else 0

    def get_output(self, last_n: int = 300) -> str:
        """Get the last N lines of output."""
        lines = self.output_lines[-last_n:]
        text = "\n".join(lines)
        # Apply token cap (~4000 tokens ≈ ~16000 chars)
        max_chars = 16000
        if len(text) > max_chars:
            # Keep first 20 lines + last 100 lines, summarize middle
            first = "\n".join(self.output_lines[:20])
            last = "\n".join(self.output_lines[-100:])
            skipped = len(self.output_lines) - 120
            text = f"{first}\n\n[... {skipped} lines omitted ...]\n\n{last}"
        return text


# ---------------------------------------------------------------------------
# Global registry of active coding agent processes
# ---------------------------------------------------------------------------

_active_processes: dict[str, CodingAgentProcess] = {}


async def handle_coding_agent(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Tool handler: spawn and run a coding sub-agent.

    This blocks the agent loop until the sub-agent completes (synchronous mode).
    The sub-agent's stdout is streamed to the SSE event queue for real-time visibility.
    """
    task = arguments.get("task", "")
    working_dir = arguments.get("working_directory", "")
    agent_type = arguments.get("agent_type", "claude")
    branch = arguments.get("branch")
    timeout_minutes = arguments.get("timeout_minutes", 30)
    agent_id = context.get("agent_id", "default")

    if not task:
        return {"error": "task is required"}
    if not working_dir:
        return {"error": "working_directory is required"}

    # Validate agent type
    if agent_type not in AGENT_COMMANDS:
        return {
            "error": f"Unknown agent_type: {agent_type}. "
            f"Supported: {list(AGENT_COMMANDS.keys())}"
        }

    # Validate required API key
    env_var = REQUIRED_ENV.get(agent_type)
    if env_var and not os.environ.get(env_var):
        return {"error": f"{agent_type} requires {env_var} to be set"}

    # Validate working directory
    dir_error = _validate_working_directory(working_dir)
    if dir_error:
        return {"error": dir_error}

    # Kill any existing process for this agent
    if agent_id in _active_processes:
        logger.info("Killing existing coding agent for %s", agent_id)
        await _active_processes[agent_id].kill()

    # Optional: checkout branch
    if branch:
        proc = await asyncio.create_subprocess_exec(
            "git", "checkout", "-B", branch,
            cwd=working_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return {"error": f"Git checkout failed: {stderr.decode().strip()}"}
        logger.info("Checked out branch %s in %s", branch, working_dir)

    # Start the coding agent
    cap = CodingAgentProcess(agent_type, task, working_dir, timeout_minutes)
    _active_processes[agent_id] = cap

    try:
        await cap.start()
    except (FileNotFoundError, ValueError) as e:
        _active_processes.pop(agent_id, None)
        return {"error": str(e)}

    # Stream output line-by-line via SSE for real-time visibility
    event_queue = context.get("event_queue")
    _stream_done = asyncio.Event()

    def _format_sse(event: str, data: Any) -> str:
        import json as _json
        return f"event: {event}\ndata: {_json.dumps(data)}\n\n"

    async def _stream() -> None:
        try:
            async for line in cap.stream_output():
                if event_queue:
                    await event_queue.put(_format_sse("coding_agent_output", {
                        "agent_type": agent_type,
                        "line": line,
                    }))
        finally:
            _stream_done.set()

    # Run streaming and wait concurrently
    stream_task = asyncio.create_task(_stream())
    exit_code = await cap.wait()

    # Give streaming a moment to flush remaining output
    try:
        await asyncio.wait_for(_stream_done.wait(), timeout=2)
    except asyncio.TimeoutError:
        pass
    stream_task.cancel()
    try:
        await stream_task
    except asyncio.CancelledError:
        pass

    _active_processes.pop(agent_id, None)

    elapsed = cap.elapsed

    # Collect git status after sub-agent finishes (if it's a git repo)
    git_summary = ""
    try:
        git_proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--stat", "HEAD",
            cwd=working_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        git_stdout, _ = await git_proc.communicate()
        if git_proc.returncode == 0 and git_stdout.strip():
            git_summary = git_stdout.decode().strip()
    except Exception:
        pass

    # Build a short summary for the final SSE done event
    status = "completed" if exit_code == 0 else "failed"
    summary_parts = [f"Coding agent ({agent_type}) {status} in {round(elapsed, 1)}s"]
    if branch:
        summary_parts.append(f"Branch: {branch}")
    if git_summary:
        summary_parts.append(f"Changes:\n{git_summary}")

    # Emit completion event
    if event_queue:
        await event_queue.put(_format_sse("coding_agent_done", {
            "status": status,
            "exit_code": exit_code,
            "agent_type": agent_type,
            "elapsed_seconds": round(elapsed, 1),
            "git_changes": git_summary or None,
        }))

    # Terminal — ends the agent loop. No further tool calls.
    return {
        "message": "\n".join(summary_parts),
        "_terminal": True,
    }


async def kill_coding_agent(agent_id: str) -> bool:
    """Kill an active coding agent. Called by interrupt handler."""
    if agent_id in _active_processes:
        await _active_processes[agent_id].kill()
        _active_processes.pop(agent_id, None)
        return True
    return False


async def kill_all_coding_agents() -> int:
    """Kill all active coding agents. Called on shutdown."""
    count = 0
    for agent_id in list(_active_processes.keys()):
        await kill_coding_agent(agent_id)
        count += 1
    return count
