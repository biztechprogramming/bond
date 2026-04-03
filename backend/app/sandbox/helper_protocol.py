"""Persistent helper process protocol for sandbox containers.

Instead of spawning a new `docker exec` for every tool call, this module
manages a long-lived helper process inside the container that accepts
JSON-RPC commands over stdin/stdout.

Usage:
    manager = HelperManager()
    result = await manager.call(container_id, "file_read", {"path": "/workspace/foo.py"})
    results = await manager.batch(container_id, [
        {"method": "file_read", "params": {"path": "a.py"}},
        {"method": "file_read", "params": {"path": "b.py"}},
    ])

Fallback:
    If the helper process dies or fails to start, callers should fall back
    to individual `docker exec` calls (the pre-Phase-2 behavior).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("bond.sandbox.helper_protocol")

# Path to the helper script inside the container
_HELPER_SCRIPT_HOST = os.path.join(
    os.path.dirname(__file__), "bond_helper.py"
)

# Timeout for individual RPC calls
_CALL_TIMEOUT = 15.0

# Timeout for helper startup (waiting for "ready" message)
_STARTUP_TIMEOUT = 10.0

# How long a helper can be idle before we consider it stale
_IDLE_TIMEOUT = 600.0  # 10 minutes

# Maximum consecutive failures before we stop trying to restart
_MAX_FAILURES = 3


@dataclass
class _HelperProcess:
    """Tracks a running helper process for a specific container."""
    container_id: str
    process: asyncio.subprocess.Process
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _request_id: int = 0
    last_used: float = field(default_factory=time.monotonic)
    failure_count: int = 0
    started_at: float = field(default_factory=time.monotonic)

    @property
    def alive(self) -> bool:
        return self.process.returncode is None

    def next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def send(self, request: dict) -> dict:
        """Send a JSON-RPC request and read the response.

        Thread-safe via asyncio.Lock — only one request at a time per helper.
        """
        async with self._lock:
            if not self.alive:
                raise RuntimeError("Helper process is dead")

            self.last_used = time.monotonic()
            req_id = self.next_id()
            request["id"] = req_id

            line = json.dumps(request, separators=(",", ":")) + "\n"
            self.process.stdin.write(line.encode())
            await self.process.stdin.drain()

            # Read response line
            try:
                raw = await asyncio.wait_for(
                    self.process.stdout.readline(), timeout=_CALL_TIMEOUT
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Helper call timed out for container %s (method=%s)",
                    self.container_id,
                    request.get("method"),
                )
                raise

            if not raw:
                raise RuntimeError("Helper process closed stdout")

            response = json.loads(raw.decode())

            # Validate response ID matches
            resp_id = response.get("id")
            if resp_id != req_id:
                logger.warning(
                    "Helper response ID mismatch: expected %d, got %s",
                    req_id,
                    resp_id,
                )

            return response

    async def close(self) -> None:
        """Terminate the helper process."""
        if self.alive:
            try:
                self.process.stdin.close()
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError, OSError):
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass


class HelperManager:
    """Manages persistent helper processes across sandbox containers.

    One helper process per container. Automatically starts helpers on first
    use and restarts them if they die (up to _MAX_FAILURES).
    """

    def __init__(self) -> None:
        self._helpers: dict[str, _HelperProcess] = {}
        self._start_locks: dict[str, asyncio.Lock] = {}

    def _get_start_lock(self, container_id: str) -> asyncio.Lock:
        if container_id not in self._start_locks:
            self._start_locks[container_id] = asyncio.Lock()
        return self._start_locks[container_id]

    async def _copy_helper_to_container(self, container_id: str) -> str:
        """Copy the helper script into the container. Returns the path inside."""
        dest = "/tmp/bond_helper.py"
        proc = await asyncio.create_subprocess_exec(
            "docker", "cp", _HELPER_SCRIPT_HOST, f"{container_id}:{dest}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode != 0:
            raise RuntimeError(
                f"Failed to copy helper to container: {stderr.decode().strip()}"
            )
        return dest

    async def _start_helper(self, container_id: str) -> _HelperProcess:
        """Start a new helper process in the container."""
        # Copy the script into the container
        script_path = await self._copy_helper_to_container(container_id)

        # Start the helper via docker exec -i (interactive stdin)
        process = await asyncio.create_subprocess_exec(
            "docker", "exec", "-i", container_id,
            "python3", script_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Wait for the "ready" message
        try:
            raw = await asyncio.wait_for(
                process.stdout.readline(), timeout=_STARTUP_TIMEOUT
            )
            if not raw:
                raise RuntimeError("Helper process exited immediately")

            ready_msg = json.loads(raw.decode())
            if not ready_msg.get("ready"):
                raise RuntimeError(f"Unexpected startup message: {ready_msg}")

            logger.info(
                "Helper started in container %s (pid=%s)",
                container_id,
                ready_msg.get("pid"),
            )
        except asyncio.TimeoutError:
            process.kill()
            raise RuntimeError("Helper process failed to start within timeout")
        except Exception:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            raise

        helper = _HelperProcess(container_id=container_id, process=process)
        return helper

    async def _get_helper(self, container_id: str) -> _HelperProcess | None:
        """Get or create a helper process for the container.

        Returns None if the helper cannot be started (too many failures).
        """
        existing = self._helpers.get(container_id)
        if existing and existing.alive:
            return existing

        # Need to start a new one
        lock = self._get_start_lock(container_id)
        async with lock:
            # Double-check after acquiring lock
            existing = self._helpers.get(container_id)
            if existing and existing.alive:
                return existing

            # Check failure count from previous helper
            prev_failures = existing.failure_count if existing else 0
            if prev_failures >= _MAX_FAILURES:
                logger.warning(
                    "Helper for container %s has failed %d times, giving up",
                    container_id,
                    prev_failures,
                )
                return None

            try:
                helper = await self._start_helper(container_id)
                helper.failure_count = prev_failures  # carry over
                self._helpers[container_id] = helper
                return helper
            except Exception as e:
                logger.warning(
                    "Failed to start helper in container %s: %s",
                    container_id,
                    e,
                )
                # Record failure
                if existing:
                    existing.failure_count += 1
                else:
                    # Create a placeholder to track failures
                    placeholder = _HelperProcess(
                        container_id=container_id,
                        process=None,  # type: ignore[arg-type]
                    )
                    placeholder.failure_count = prev_failures + 1
                    self._helpers[container_id] = placeholder
                return None

    async def call(
        self,
        container_id: str,
        method: str,
        params: dict | None = None,
    ) -> dict | None:
        """Send a single RPC call to the helper.

        Returns the response dict, or None if the helper is unavailable
        (caller should fall back to docker exec).
        """
        helper = await self._get_helper(container_id)
        if helper is None:
            return None

        try:
            response = await helper.send({
                "method": method,
                "params": params or {},
            })
            helper.failure_count = 0  # reset on success
            return response
        except Exception as e:
            logger.warning(
                "Helper call failed for container %s: %s", container_id, e
            )
            helper.failure_count += 1
            # Kill the broken helper so next call restarts it
            await helper.close()
            return None

    async def batch(
        self,
        container_id: str,
        calls: list[dict],
    ) -> list[dict] | None:
        """Send a batch of RPC calls to the helper.

        Returns a list of response dicts, or None if helper unavailable.
        """
        helper = await self._get_helper(container_id)
        if helper is None:
            return None

        try:
            response = await helper.send({
                "method": "batch",
                "params": {"calls": calls},
            })
            helper.failure_count = 0
            result = response.get("result")
            if isinstance(result, list):
                return result
            # If there's an error at the batch level
            if "error" in response:
                logger.warning("Batch error: %s", response["error"])
                return None
            return None
        except Exception as e:
            logger.warning(
                "Helper batch call failed for container %s: %s",
                container_id,
                e,
            )
            helper.failure_count += 1
            await helper.close()
            return None

    async def close_helper(self, container_id: str) -> None:
        """Shut down the helper for a specific container."""
        helper = self._helpers.pop(container_id, None)
        if helper:
            await helper.close()
        self._start_locks.pop(container_id, None)

    async def close_all(self) -> None:
        """Shut down all helper processes."""
        for helper in self._helpers.values():
            await helper.close()
        self._helpers.clear()
        self._start_locks.clear()

    async def cleanup_idle(self, max_idle: float = _IDLE_TIMEOUT) -> int:
        """Close helpers that have been idle too long. Returns count closed."""
        now = time.monotonic()
        to_close = [
            cid
            for cid, h in self._helpers.items()
            if (now - h.last_used) > max_idle
        ]
        for cid in to_close:
            await self.close_helper(cid)
        if to_close:
            logger.info("Cleaned up %d idle helpers", len(to_close))
        return len(to_close)

    def is_available(self, container_id: str) -> bool:
        """Check if a helper is currently running for this container."""
        helper = self._helpers.get(container_id)
        return helper is not None and helper.alive

    def has_failed(self, container_id: str) -> bool:
        """Check if helper has exceeded max failures for this container."""
        helper = self._helpers.get(container_id)
        if helper is None:
            return False
        return helper.failure_count >= _MAX_FAILURES


# Module-level singleton
_manager: HelperManager | None = None


def get_helper_manager() -> HelperManager:
    """Get the global HelperManager singleton."""
    global _manager
    if _manager is None:
        _manager = HelperManager()
    return _manager
