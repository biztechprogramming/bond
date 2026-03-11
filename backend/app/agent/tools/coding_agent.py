"""Coding agent tool — spawns a non-blocking coding sub-agent process.

The sub-agent runs in the background. The tool returns immediately so the
agent loop (and user) can continue interacting with Bond. A background
git-diff watcher detects incremental file changes and pushes them as SSE
events to the frontend via a dedicated streaming endpoint on the worker.

Design: each change is shown exactly once. The watcher tracks per-file diff
hashes and only emits when a file's diff against the baseline changes.
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
    ):
        self.process = process
        self.watcher = watcher
        self.conversation_id = conversation_id
        self.agent_type = agent_type
        self.baseline_commit = baseline_commit
        self.branch = branch
        self.event_queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self._monitor_task: asyncio.Task | None = None
        self.started_at = time.time()
        self.finished = False
        self.exit_code: int | None = None
        self.final_summary: str = ""

    def start_monitor(self) -> None:
        """Start the background diff watcher + process monitor."""
        self._monitor_task = asyncio.create_task(self._monitor())

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
            })

            # Enqueue system event to SpacetimeDB so the gateway can
            # trigger a completion turn — the LLM will summarize results
            # and respond to the user automatically.
            await self._enqueue_system_event(status, stat)

        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Coding agent monitor crashed")
            await self.event_queue.put({
                "type": "error",
                "message": "Coding agent monitor crashed unexpectedly",
            })
        finally:
            # Sentinel — signals end of event stream
            await self.event_queue.put(None)

    async def _enqueue_system_event(self, status: str, git_stat: str) -> None:
        """Write a system event to SpacetimeDB for the gateway to pick up."""
        try:
            import uuid
            from backend.app.core.spacetimedb import get_stdb

            stdb = get_stdb()
            event_type = "coding_agent_done" if self.exit_code == 0 else "coding_agent_failed"
            metadata_json = json.dumps({
                "agent_type": self.agent_type,
                "exit_code": self.exit_code,
                "elapsed_seconds": round(self.process.elapsed, 1),
                "git_stat": git_stat,
                "baseline_commit": self.baseline_commit,
                "branch": self.branch,
                "working_directory": self.process.working_directory,
            })
            success = await stdb.call_reducer("enqueue_system_event", [
                str(uuid.uuid4()),       # id
                self.conversation_id,    # conversationId
                "",                      # agentId (resolved by gateway)
                event_type,              # eventType
                self.final_summary,      # summary
                metadata_json,           # metadata
            ])
            if success:
                logger.info(
                    "System event enqueued: %s for conversation %s",
                    event_type, self.conversation_id,
                )
            else:
                logger.warning(
                    "Failed to enqueue system event (reducer returned false) for %s",
                    self.conversation_id,
                )
        except Exception as e:
            logger.warning("Failed to enqueue system event to SpacetimeDB: %s", e)

    async def stop(self) -> None:
        """Kill the process and cancel the monitor."""
        await self.process.kill()
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        self.finished = True


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

    # Create the session with diff watcher
    watcher = GitDiffWatcher(working_dir, baseline)
    session = CodingAgentSession(
        process=cap,
        watcher=watcher,
        conversation_id=conversation_id,
        agent_type=agent_type,
        baseline_commit=baseline,
        branch=branch,
    )
    _active_sessions[agent_id] = session
    session.start_monitor()

    logger.info(
        "Coding agent started: agent_id=%s type=%s baseline=%s dir=%s",
        agent_id, agent_type, baseline[:8], working_dir,
    )

    # Return immediately — non-blocking, non-terminal
    return {
        "status": "started",
        "agent_type": agent_type,
        "working_directory": working_dir,
        "baseline_commit": baseline[:8],
        "message": (
            f"Coding agent ({agent_type}) started in {working_dir}. "
            f"It will work in the background — you can continue chatting. "
            f"Changes will appear in the UI as they happen."
        ),
    }


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
