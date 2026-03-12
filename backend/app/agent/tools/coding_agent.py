"""Coding agent tool — spawns a non-blocking coding sub-agent process.

The sub-agent runs in the background. The tool returns immediately so the
agent loop (and user) can continue interacting with Bond. A background
git-diff watcher detects incremental file changes and pushes them as SSE
events to the frontend via a dedicated streaming endpoint on the worker.

Design: each change is shown exactly once. The watcher tracks per-file diff
hashes and only emits when a file's diff against the baseline changes.

Stdout capture: agent stdout is captured and optionally (a) written to a log
file on disk, and (b) streamed as ``output`` events through the SSE event
queue.  Both channels are independently toggleable via settings:
  - ``coding_agent.log_to_file``   (default: true)
  - ``coding_agent.stream_output`` (default: true)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import signal
import time
from pathlib import Path
from typing import Any, AsyncIterator

logger = logging.getLogger("bond.agent.tools.coding_agent")

# ---------------------------------------------------------------------------
# Output-capture settings (read from env / settings DB at spawn time)
# ---------------------------------------------------------------------------

# Directory where per-session log files are written
LOG_DIR = Path(os.environ.get("BOND_CODING_AGENT_LOG_DIR", "/tmp/bond-coding-agent-logs"))

# Defaults — can be overridden per-session via settings DB or env vars
DEFAULT_LOG_TO_FILE = True
DEFAULT_STREAM_OUTPUT = True

# Max bytes buffered before flushing a chunk to the event queue
OUTPUT_CHUNK_SIZE = 4096
# Max seconds between flushes (even if the buffer isn't full)
OUTPUT_FLUSH_INTERVAL = 2.0

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

# Allowed workspace roots
DEFAULT_ALLOWED_ROOTS = ["/home", "/workspace", "/tmp", "/bond"]

# Git diff polling interval
DIFF_POLL_INTERVAL_SECONDS = 10


def _get_allowed_roots() -> list[str]:
    env_roots = os.environ.get("BOND_ALLOWED_WORKSPACE_ROOTS")
    if env_roots:
        return [r.strip() for r in env_roots.split(":") if r.strip()]
    return DEFAULT_ALLOWED_ROOTS


def _validate_working_directory(working_dir: str) -> str | None:
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
# Git diff watcher
# ---------------------------------------------------------------------------


async def _git_head(cwd: str) -> str | None:
    """Get the current HEAD commit hash."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "HEAD",
            cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            return stdout.decode().strip()
    except Exception:
        pass
    return None


async def _git_diff_stat(cwd: str, baseline: str) -> str:
    """Get git diff --stat against baseline."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--stat", baseline,
            cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            return stdout.decode().strip()
    except Exception:
        pass
    return ""


async def _git_diff_per_file(cwd: str, baseline: str) -> dict[str, str]:
    """Get per-file diffs against baseline. Returns {filepath: diff_text}."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "diff", baseline,
            cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return {}
    except Exception:
        return {}

    raw = stdout.decode("utf-8", errors="replace")
    # Also pick up new untracked files
    try:
        proc2 = await asyncio.create_subprocess_exec(
            "git", "diff", "--no-index", "/dev/null", ".",
            cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        # This always returns 1 if there are differences, which is expected
        untracked_stdout, _ = await proc2.communicate()
    except Exception:
        untracked_stdout = b""

    # Parse into per-file chunks
    files: dict[str, str] = {}
    current_file = None
    current_lines: list[str] = []

    for line in raw.split("\n"):
        if line.startswith("diff --git"):
            if current_file and current_lines:
                files[current_file] = "\n".join(current_lines)
            # Extract filename: "diff --git a/foo.txt b/foo.txt" -> "foo.txt"
            parts = line.split(" b/", 1)
            current_file = parts[1] if len(parts) > 1 else None
            current_lines = [line]
        elif current_file:
            current_lines.append(line)

    if current_file and current_lines:
        files[current_file] = "\n".join(current_lines)

    return files


class GitDiffWatcher:
    """Polls git diff against a baseline commit and emits only new/changed diffs."""

    def __init__(self, working_dir: str, baseline_commit: str):
        self.working_dir = working_dir
        self.baseline = baseline_commit
        self._sent_hashes: dict[str, str] = {}  # filepath -> md5 of last sent diff

    async def poll(self) -> dict[str, str]:
        """Return diffs for files that changed since last poll.

        Returns {filepath: diff_text} only for files whose diff is new or changed.
        """
        file_diffs = await _git_diff_per_file(self.working_dir, self.baseline)
        new_diffs: dict[str, str] = {}

        for path, diff_text in file_diffs.items():
            h = hashlib.md5(diff_text.encode()).hexdigest()
            if self._sent_hashes.get(path) != h:
                new_diffs[path] = diff_text
                self._sent_hashes[path] = h

        return new_diffs


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
        self.start_time: float = 0
        self._killed = False
        self._pty_reader: asyncio.StreamReader | None = None
        self._pty_transport: asyncio.BaseTransport | None = None
        self._master_fd: int | None = None

    async def start(self) -> None:
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
            self.agent_type, self.working_directory, self.timeout,
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
        import pty as pty_mod
        master_fd, slave_fd = pty_mod.openpty()
        self._master_fd = master_fd
        env["TERM"] = "xterm-256color"
        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.working_directory,
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            env=env,
        )
        os.close(slave_fd)
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        loop = asyncio.get_event_loop()
        transport, _ = await loop.connect_read_pipe(
            lambda: protocol, os.fdopen(master_fd, "rb", closefd=False)
        )
        self._pty_reader = reader
        self._pty_transport = transport

    @property
    def output_reader(self) -> asyncio.StreamReader | None:
        """Return the stream reader for the sub-agent's stdout/pty output."""
        if self._pty_reader:
            return self._pty_reader
        if self.process and self.process.stdout:
            return self.process.stdout
        return None

    def is_running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def wait(self) -> int:
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


# ---------------------------------------------------------------------------
# Active sessions — tracks running agents + their event queues
# ---------------------------------------------------------------------------


class CodingAgentSession:
    """Tracks an active coding agent and its background diff watcher."""

    def __init__(
        self,
        process: CodingAgentProcess,
        watcher: GitDiffWatcher,
        conversation_id: str,
        agent_type: str,
        baseline_commit: str,
        branch: str | None = None,
        *,
        log_to_file: bool = DEFAULT_LOG_TO_FILE,
        stream_output: bool = DEFAULT_STREAM_OUTPUT,
    ):
        self.process = process
        self.watcher = watcher
        self.conversation_id = conversation_id
        self.agent_type = agent_type
        self.baseline_commit = baseline_commit
        self.branch = branch
        self.event_queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self._monitor_task: asyncio.Task | None = None
        self._output_task: asyncio.Task | None = None
        self.started_at = time.time()
        self.finished = False
        self.exit_code: int | None = None
        self.final_summary: str = ""

        # Output capture settings
        self.log_to_file = log_to_file
        self.stream_output = stream_output
        self.log_path: Path | None = None
        self._log_file_handle = None
        self._output_buffer: list[str] = []  # In-memory rolling buffer (last N lines)
        self._output_buffer_max = 500  # Keep last 500 lines in memory

    def start_monitor(self) -> None:
        """Start the background diff watcher + process monitor + output reader."""
        self._monitor_task = asyncio.create_task(self._monitor())
        # Start stdout reader if either capture mode is enabled
        if self.log_to_file or self.stream_output:
            self._output_task = asyncio.create_task(self._read_output())

    async def _open_log_file(self) -> None:
        """Create the log directory and open the log file for writing."""
        if not self.log_to_file:
            return
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d-%H%M%S")
            self.log_path = LOG_DIR / f"{self.agent_type}-{self.conversation_id[:8]}-{ts}.log"
            self._log_file_handle = open(self.log_path, "w", buffering=1)  # line-buffered
            logger.info("Coding agent log: %s", self.log_path)
        except Exception as e:
            logger.warning("Failed to open coding agent log file: %s", e)
            self._log_file_handle = None

    def _close_log_file(self) -> None:
        if self._log_file_handle:
            try:
                self._log_file_handle.close()
            except Exception:
                pass
            self._log_file_handle = None

    async def _read_output(self) -> None:
        """Background task: read sub-agent stdout and dispatch to file / event queue."""
        reader = self.process.output_reader
        if not reader:
            logger.debug("No output reader available for coding agent")
            return

        await self._open_log_file()

        buffer = ""
        last_flush = time.monotonic()

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(reader.read(OUTPUT_CHUNK_SIZE), timeout=OUTPUT_FLUSH_INTERVAL)
                except asyncio.TimeoutError:
                    # Flush whatever we have buffered
                    if buffer:
                        await self._emit_output(buffer)
                        buffer = ""
                        last_flush = time.monotonic()
                    continue

                if not chunk:
                    # EOF — process has closed its stdout
                    if buffer:
                        await self._emit_output(buffer)
                    break

                text = chunk.decode("utf-8", errors="replace")
                buffer += text

                # Flush if buffer is big enough or enough time has passed
                now = time.monotonic()
                if len(buffer) >= OUTPUT_CHUNK_SIZE or (now - last_flush) >= OUTPUT_FLUSH_INTERVAL:
                    await self._emit_output(buffer)
                    buffer = ""
                    last_flush = now

        except asyncio.CancelledError:
            if buffer:
                await self._emit_output(buffer)
        except Exception as e:
            logger.debug("Output reader error: %s", e)
        finally:
            self._close_log_file()

    async def _emit_output(self, text: str) -> None:
        """Write output text to the log file and/or event queue."""
        # Write to log file
        if self.log_to_file and self._log_file_handle:
            try:
                self._log_file_handle.write(text)
            except Exception as e:
                logger.debug("Log write error: %s", e)

        # Keep in-memory buffer
        lines = text.splitlines(keepends=True)
        self._output_buffer.extend(lines)
        # Trim to max
        if len(self._output_buffer) > self._output_buffer_max:
            self._output_buffer = self._output_buffer[-self._output_buffer_max:]

        # Stream to event queue
        if self.stream_output:
            await self.event_queue.put({
                "type": "output",
                "text": text,
            })

    def get_recent_output(self, lines: int = 50) -> str:
        """Return the last N lines from the in-memory output buffer."""
        return "".join(self._output_buffer[-lines:])

    async def _monitor(self) -> None:
        """Background task: poll git diffs and wait for process exit."""
        try:
            while self.process.is_running():
                # Poll for new diffs
                try:
                    new_diffs = await self.watcher.poll()
                    for filepath, diff_text in new_diffs.items():
                        await self.event_queue.put({
                            "type": "diff",
                            "file": filepath,
                            "diff": diff_text,
                        })
                except Exception as e:
                    logger.debug("Diff poll error: %s", e)

                # Wait before next poll, but check process status frequently
                for _ in range(DIFF_POLL_INTERVAL_SECONDS):
                    if not self.process.is_running():
                        break
                    await asyncio.sleep(1)

            # Wait for the output reader to finish draining
            if self._output_task and not self._output_task.done():
                try:
                    await asyncio.wait_for(self._output_task, timeout=5)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass

            # Process finished — get exit code
            self.exit_code = self.process.process.returncode if self.process.process else -1
            self.finished = True

            # Final diff poll to catch any last changes
            try:
                final_diffs = await self.watcher.poll()
                for filepath, diff_text in final_diffs.items():
                    await self.event_queue.put({
                        "type": "diff",
                        "file": filepath,
                        "diff": diff_text,
                    })
            except Exception:
                pass

            # Build summary
            elapsed = self.process.elapsed
            status = "completed" if self.exit_code == 0 else "failed"
            stat = await _git_diff_stat(
                self.process.working_directory, self.baseline_commit
            )
            parts = [f"Coding agent ({self.agent_type}) {status} in {round(elapsed, 1)}s"]
            if self.branch:
                parts.append(f"Branch: {self.branch}")
            if self.log_path and self.log_path.exists():
                parts.append(f"Log: {self.log_path}")
            if stat:
                parts.append(f"\n```\n{stat}\n```")
            self.final_summary = "\n".join(parts)

            await self.event_queue.put({
                "type": "done",
                "status": status,
                "exit_code": self.exit_code,
                "elapsed_seconds": round(elapsed, 1),
                "summary": self.final_summary,
                "git_stat": stat,
                "log_path": str(self.log_path) if self.log_path else None,
            })

        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Coding agent monitor crashed")
            await self.event_queue.put({
                "type": "error",
                "message": "Coding agent monitor crashed unexpectedly",
            })
        finally:
            self._close_log_file()
            # Sentinel — signals end of event stream
            await self.event_queue.put(None)

    async def stop(self) -> None:
        """Kill the process and cancel the monitor."""
        await self.process.kill()
        if self._output_task and not self._output_task.done():
            self._output_task.cancel()
            try:
                await self._output_task
            except asyncio.CancelledError:
                pass
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        self._close_log_file()
        self.finished = True


def _bool_setting(db_val: str | None, env_val: str | None, default: bool) -> bool:
    """Return the first non-None setting, parsed as a boolean string."""
    for val in (db_val, env_val):
        if val is not None:
            return val.lower() in ("true", "1", "yes", "on")
    return default


# Global registry: agent_id -> CodingAgentSession
_active_sessions: dict[str, CodingAgentSession] = {}


def get_active_session(agent_id: str) -> CodingAgentSession | None:
    """Get an active coding agent session (used by the SSE endpoint)."""
    return _active_sessions.get(agent_id)


def get_session_by_conversation(conversation_id: str) -> CodingAgentSession | None:
    """Find active session by conversation ID."""
    for session in _active_sessions.values():
        if session.conversation_id == conversation_id:
            return session
    return None


# ---------------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------------


async def handle_coding_agent(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Spawn a coding sub-agent in the background. Returns immediately.

    The sub-agent runs asynchronously. A git-diff watcher monitors changes
    and pushes incremental diffs to the frontend via SSE. The user can
    continue interacting with Bond while the agent works.
    """
    task = arguments.get("task", "")
    working_dir = arguments.get("working_directory", "")
    agent_type = arguments.get("agent_type", "claude")
    branch = arguments.get("branch")
    timeout_minutes = arguments.get("timeout_minutes", 30)
    agent_id = context.get("agent_id", "default")
    conversation_id = context.get("conversation_id", "")

    if not task:
        return {"error": "task is required"}
    if not working_dir:
        return {"error": "working_directory is required"}

    if agent_type not in AGENT_COMMANDS:
        return {
            "error": f"Unknown agent_type: {agent_type}. "
            f"Supported: {list(AGENT_COMMANDS.keys())}"
        }

    # Resolve API key
    env_var = REQUIRED_ENV.get(agent_type)
    resolved_api_key: str | None = None
    if env_var:
        injected_keys: dict[str, str] = context.get("api_keys", {})
        _env_to_provider = {
            "ANTHROPIC_API_KEY": "anthropic",
            "OPENAI_API_KEY": "openai",
        }
        provider = _env_to_provider.get(env_var, "")
        resolved_api_key = injected_keys.get(provider) or os.environ.get(env_var)
        if not resolved_api_key:
            return {"error": f"{agent_type} requires {env_var} to be set"}

    dir_error = _validate_working_directory(working_dir)
    if dir_error:
        return {"error": dir_error}

    # Kill any existing session for this agent
    if agent_id in _active_sessions:
        logger.info("Killing existing coding agent for %s", agent_id)
        await _active_sessions[agent_id].stop()
        _active_sessions.pop(agent_id, None)

    # Capture git baseline before making changes
    baseline = await _git_head(working_dir)
    if not baseline:
        baseline = "HEAD"  # fallback — diffing against HEAD works for uncommitted changes

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

    # Create and start the process
    cap = CodingAgentProcess(agent_type, task, working_dir, timeout_minutes)

    if resolved_api_key and env_var:
        os.environ[env_var] = resolved_api_key

    try:
        await cap.start()
    except (FileNotFoundError, ValueError) as e:
        return {"error": str(e)}

    # Read output capture settings from context (injected by worker from settings DB)
    coding_agent_settings: dict[str, str] = context.get("coding_agent_settings", {})
    log_to_file = _bool_setting(
        coding_agent_settings.get("coding_agent.log_to_file"),
        os.environ.get("BOND_CODING_AGENT_LOG_TO_FILE"),
        DEFAULT_LOG_TO_FILE,
    )
    stream_output = _bool_setting(
        coding_agent_settings.get("coding_agent.stream_output"),
        os.environ.get("BOND_CODING_AGENT_STREAM_OUTPUT"),
        DEFAULT_STREAM_OUTPUT,
    )

    # Create the session with diff watcher
    watcher = GitDiffWatcher(working_dir, baseline)
    session = CodingAgentSession(
        process=cap,
        watcher=watcher,
        conversation_id=conversation_id,
        agent_type=agent_type,
        baseline_commit=baseline,
        branch=branch,
        log_to_file=log_to_file,
        stream_output=stream_output,
    )
    _active_sessions[agent_id] = session
    session.start_monitor()

    logger.info(
        "Coding agent started: agent_id=%s type=%s baseline=%s dir=%s "
        "log_to_file=%s stream_output=%s",
        agent_id, agent_type, baseline[:8], working_dir,
        log_to_file, stream_output,
    )

    # Return immediately — non-blocking, non-terminal
    log_msg = ""
    if log_to_file:
        log_msg = f" Output is being logged to disk."
    if stream_output:
        log_msg += f" Live output is streaming to the UI."

    return {
        "status": "started",
        "agent_type": agent_type,
        "working_directory": working_dir,
        "baseline_commit": baseline[:8],
        "log_to_file": log_to_file,
        "stream_output": stream_output,
        "message": (
            f"Coding agent ({agent_type}) started in {working_dir}. "
            f"It will work in the background — you can continue chatting. "
            f"Changes will appear in the UI as they happen.{log_msg}"
        ),
    }


def get_coding_agent_status(agent_id: str | None = None) -> dict[str, Any]:
    """Return status info about active coding agent sessions.

    If agent_id is given, return status for that specific session.
    Otherwise, return a summary of all active sessions.
    """
    if agent_id and agent_id in _active_sessions:
        session = _active_sessions[agent_id]
        return {
            "agent_id": agent_id,
            "agent_type": session.agent_type,
            "conversation_id": session.conversation_id,
            "running": session.process.is_running(),
            "finished": session.finished,
            "exit_code": session.exit_code,
            "elapsed_seconds": round(session.process.elapsed, 1),
            "log_to_file": session.log_to_file,
            "stream_output": session.stream_output,
            "log_path": str(session.log_path) if session.log_path else None,
            "recent_output": session.get_recent_output(30),
        }

    # All sessions
    sessions = []
    for aid, session in _active_sessions.items():
        sessions.append({
            "agent_id": aid,
            "agent_type": session.agent_type,
            "running": session.process.is_running(),
            "finished": session.finished,
            "elapsed_seconds": round(session.process.elapsed, 1),
        })
    return {"active_sessions": sessions, "count": len(sessions)}


async def kill_coding_agent(agent_id: str) -> bool:
    """Kill an active coding agent. Called by interrupt handler."""
    if agent_id in _active_sessions:
        await _active_sessions[agent_id].stop()
        _active_sessions.pop(agent_id, None)
        return True
    return False


async def kill_all_coding_agents() -> int:
    """Kill all active coding agents. Called on shutdown."""
    count = 0
    for agent_id in list(_active_sessions.keys()):
        await kill_coding_agent(agent_id)
        count += 1
    return count
