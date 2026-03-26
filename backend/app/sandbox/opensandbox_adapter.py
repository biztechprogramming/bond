"""OpenSandboxAdapter — routes sandbox operations to an OpenSandbox server.

Implements the same interface as SandboxManager but talks to the OpenSandbox
lifecycle API (POST /v1/sandboxes, etc.) and the execd API (POST /code,
POST /command, file operations, etc.) instead of calling Docker directly.

Feature-flagged via bond.json: {"sandbox_backend": "opensandbox"}.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, AsyncIterator

import httpx

logger = logging.getLogger("bond.sandbox.opensandbox")

# Default ports matching OpenSandbox docker-compose example
_DEFAULT_SERVER_PORT = 8090
_DEFAULT_EXECD_PORT = 44772


class OpenSandboxAdapter:
    """Manages sandboxes via the OpenSandbox server API."""

    def __init__(
        self,
        server_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.server_url = (
            server_url
            or os.environ.get("OPENSANDBOX_SERVER_URL")
            or f"http://localhost:{_DEFAULT_SERVER_PORT}"
        )
        self.api_key = api_key or os.environ.get("OPEN_SANDBOX_API_KEY", "")

        # sandbox_id -> tracking info
        self._sandboxes: dict[str, dict[str, Any]] = {}
        # agent_key -> sandbox_id mapping
        self._agent_sandbox_map: dict[str, str] = {}
        # Per-agent locks
        self._agent_locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _lifecycle_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["OPEN-SANDBOX-API-KEY"] = self.api_key
        return headers

    def _execd_headers(self, access_token: str = "") -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if access_token:
            headers["X-EXECD-ACCESS-TOKEN"] = access_token
        return headers

    def _lifecycle_url(self, path: str) -> str:
        return f"{self.server_url}/v1{path}"

    def _execd_url(self, sandbox_id: str, path: str) -> str:
        """Build the execd URL for a sandbox.

        The execd endpoint is discovered via the sandbox's endpoint API
        and cached in _sandboxes[sandbox_id]["execd_url"].
        """
        info = self._sandboxes.get(sandbox_id, {})
        base = info.get("execd_url", "")
        if not base:
            raise RuntimeError(
                f"No execd URL for sandbox {sandbox_id}. "
                "Was the sandbox created and started?"
            )
        return f"{base}{path}"

    def _get_agent_lock(self, agent_key: str) -> asyncio.Lock:
        if agent_key not in self._agent_locks:
            self._agent_locks[agent_key] = asyncio.Lock()
        return self._agent_locks[agent_key]

    # ------------------------------------------------------------------
    # Image introspection
    # ------------------------------------------------------------------

    async def _resolve_image_entrypoint(self, image: str) -> list[str]:
        """Inspect a Docker image to get its ENTRYPOINT + CMD.

        Falls back to ["/bin/bash"] if inspection fails.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "inspect", "--format",
                "{{json .Config.Entrypoint}}|{{json .Config.Cmd}}",
                image,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                logger.warning("Could not inspect image %s, using /bin/bash", image)
                return ["/bin/bash"]

            parts = stdout.decode().strip().split("|", 1)
            ep = json.loads(parts[0]) if parts[0] != "null" else []
            cmd = json.loads(parts[1]) if len(parts) > 1 and parts[1] != "null" else []

            combined = (ep or []) + (cmd or [])
            if not combined:
                return ["/bin/bash"]
            return combined
        except Exception as e:
            logger.warning("Failed to inspect image %s: %s", image, e)
            return ["/bin/bash"]

    # ------------------------------------------------------------------
    # Sandbox lifecycle (matches SandboxManager.ensure_running interface)
    # ------------------------------------------------------------------

    async def ensure_running(self, agent: dict) -> dict[str, Any]:
        """Ensure an OpenSandbox is running for the given agent.

        Returns {"worker_url": str, "sandbox_id": str}.
        """
        agent_id = agent["id"]
        agent_name = agent.get("name", "agent").lower().replace(" ", "-")
        key = f"bond-{agent_name}-{agent_id}"
        lock = self._get_agent_lock(key)

        async with lock:
            # Check existing sandbox
            if key in self._agent_sandbox_map:
                sandbox_id = self._agent_sandbox_map[key]
                state = await self._get_sandbox_state(sandbox_id)
                if state == "Running":
                    self._sandboxes[sandbox_id]["last_used"] = time.time()
                    return {
                        "worker_url": self._sandboxes[sandbox_id].get("execd_url", ""),
                        "sandbox_id": sandbox_id,
                    }
                elif state in ("Paused", "Pausing"):
                    await self._resume_sandbox(sandbox_id)
                    self._sandboxes[sandbox_id]["last_used"] = time.time()
                    return {
                        "worker_url": self._sandboxes[sandbox_id].get("execd_url", ""),
                        "sandbox_id": sandbox_id,
                    }
                else:
                    # Terminated/Failed — clean up and recreate
                    await self._cleanup_tracking(key, sandbox_id)

            # Create new sandbox
            sandbox_id = await self._create_sandbox(agent, key)
            return {
                "worker_url": self._sandboxes[sandbox_id].get("execd_url", ""),
                "sandbox_id": sandbox_id,
            }

    async def _create_sandbox(self, agent: dict, key: str) -> str:
        """Create a new OpenSandbox for the agent."""
        agent_id = agent["id"]
        sandbox_image = agent.get("sandbox_image", "python:3.12-slim")
        workspace_mounts = agent.get("workspace_mounts", [])

        # Build volume specs
        volumes = []
        for i, mount in enumerate(workspace_mounts):
            host_path = os.path.expanduser(mount.get("host_path", ""))
            mount_name = mount.get("mount_name") or ""
            container_path = mount.get("container_path") or f"/workspace/{mount_name or 'workspace'}"
            readonly = mount.get("readonly", False)

            # Sanitize mount_name to match opensandbox pattern: ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$
            # Strip leading dots/dashes, replace invalid chars with dashes
            sanitized = mount_name.lower().lstrip(".-")
            sanitized = re.sub(r"[^a-z0-9-]", "-", sanitized)
            sanitized = sanitized.strip("-")
            if not sanitized:
                # Derive from host_path basename or use index-based fallback
                sanitized = re.sub(r"[^a-z0-9-]", "-", os.path.basename(host_path).lower().lstrip(".-")).strip("-")
            if not sanitized:
                sanitized = f"vol-{i}"

            volumes.append({
                "name": sanitized,
                "host": {"path": host_path},
                "mountPath": container_path,
                "readOnly": readonly,
            })

        # Resolve entrypoint: use agent-supplied override, or inspect the
        # image to discover its ENTRYPOINT + CMD so we don't clobber it.
        entrypoint = agent.get("entrypoint")
        if not entrypoint:
            entrypoint = await self._resolve_image_entrypoint(sandbox_image)

        create_body: dict[str, Any] = {
            "image": {"uri": sandbox_image},
            "timeout": 3600,
            "resourceLimits": {
                "cpu": agent.get("resource_limits", {}).get("cpu", "1000m"),
                "memory": agent.get("resource_limits", {}).get("memory", "512Mi"),
            },
            "entrypoint": entrypoint,
            "metadata": {
                "name": key,
                "agent_id": agent_id,
                "bond_managed": "true",
            },
        }

        if volumes:
            create_body["volumes"] = volumes

        # Environment variables — merge agent-specific env with system env
        env = dict(agent.get("env", {}))
        # REMOVED: SpacetimeDB token injection (2026-03-12)
        # Agents must NOT have direct SpacetimeDB access — see design docs 035, 039.

        # Forward Langfuse config from the host process so agent workers
        # report to the correct Langfuse instance without baking creds
        # into the Docker image.
        for _lf_key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
            _lf_val = os.environ.get(_lf_key)
            if _lf_val and _lf_key not in env:
                env[_lf_key] = _lf_val

        if env:
            create_body["env"] = env

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                self._lifecycle_url("/sandboxes"),
                json=create_body,
                headers=self._lifecycle_headers(),
            )
            if resp.status_code not in (200, 201, 202):
                raise RuntimeError(
                    f"Failed to create OpenSandbox for {agent_id}: "
                    f"HTTP {resp.status_code} — {resp.text}"
                )

            data = resp.json()
            sandbox_id = data["id"]

        # Wait for Running state
        await self._wait_for_state(sandbox_id, "Running", timeout=60.0)

        # Discover the execd endpoint
        execd_url = await self._discover_execd_endpoint(sandbox_id)

        # Track
        self._sandboxes[sandbox_id] = {
            "sandbox_id": sandbox_id,
            "agent_key": key,
            "execd_url": execd_url,
            "last_used": time.time(),
        }
        self._agent_sandbox_map[key] = sandbox_id

        logger.info(
            "Created OpenSandbox %s for agent %s (execd=%s)",
            sandbox_id, agent_id, execd_url,
        )
        return sandbox_id

    async def _wait_for_state(
        self,
        sandbox_id: str,
        target_state: str,
        timeout: float = 60.0,
        interval: float = 1.0,
    ) -> None:
        """Poll sandbox until it reaches target_state or timeout."""
        start = time.monotonic()
        while True:
            elapsed = time.monotonic() - start
            if elapsed >= timeout:
                raise RuntimeError(
                    f"Sandbox {sandbox_id} did not reach {target_state} "
                    f"within {timeout}s"
                )
            state = await self._get_sandbox_state(sandbox_id)
            if state == target_state:
                return
            if state in ("Failed", "Terminated"):
                raise RuntimeError(
                    f"Sandbox {sandbox_id} entered {state} while waiting for {target_state}"
                )
            await asyncio.sleep(interval)

    async def _get_sandbox_state(self, sandbox_id: str) -> str:
        """Fetch current sandbox state from the lifecycle API."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                self._lifecycle_url(f"/sandboxes/{sandbox_id}"),
                headers=self._lifecycle_headers(),
            )
            if resp.status_code == 404:
                return "Terminated"
            resp.raise_for_status()
            data = resp.json()
            return data.get("status", {}).get("state", "Unknown")

    async def _discover_execd_endpoint(self, sandbox_id: str) -> str:
        """Get the execd endpoint URL for a sandbox via the server API."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                self._lifecycle_url(f"/sandboxes/{sandbox_id}/endpoints/{_DEFAULT_EXECD_PORT}"),
                headers=self._lifecycle_headers(),
            )
            if resp.status_code == 200:
                data = resp.json()
                endpoint = data.get("endpoint", "")
                if endpoint and not endpoint.startswith("http"):
                    endpoint = f"http://{endpoint}"
                return endpoint

        # Fallback: construct from server URL pattern
        # In docker-compose sidecar mode, execd is reachable via the server
        return f"{self.server_url}/sandboxes/{sandbox_id}/execd"

    async def _resume_sandbox(self, sandbox_id: str) -> None:
        """Resume a paused sandbox."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                self._lifecycle_url(f"/sandboxes/{sandbox_id}/resume"),
                headers=self._lifecycle_headers(),
            )
            if resp.status_code not in (200, 202):
                raise RuntimeError(
                    f"Failed to resume sandbox {sandbox_id}: "
                    f"HTTP {resp.status_code} — {resp.text}"
                )
        await self._wait_for_state(sandbox_id, "Running", timeout=30.0)

    async def _cleanup_tracking(self, key: str, sandbox_id: str) -> None:
        """Remove tracking for a sandbox."""
        self._sandboxes.pop(sandbox_id, None)
        self._agent_sandbox_map.pop(key, None)
        self._agent_locks.pop(key, None)

    # ------------------------------------------------------------------
    # Legacy-compatible container interface
    # ------------------------------------------------------------------

    async def get_or_create_container(
        self,
        agent_id: str,
        sandbox_image: str,
        workspace_mounts: list[dict[str, str]] | None = None,
        agent_name: str = "agent",
    ) -> str:
        """Compatibility shim — creates an OpenSandbox and returns its ID."""
        agent = {
            "id": agent_id,
            "name": agent_name,
            "sandbox_image": sandbox_image,
            "workspace_mounts": workspace_mounts or [],
        }
        result = await self.ensure_running(agent)
        return result["sandbox_id"]

    # ------------------------------------------------------------------
    # Code execution (stateful via code interpreter)
    # ------------------------------------------------------------------

    async def execute_code(
        self,
        sandbox_id: str,
        language: str,
        code: str,
        context_id: str | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        """Execute code via the OpenSandbox code interpreter API.

        Returns {"exit_code": int, "stdout": str, "stderr": str, "result": ...}.
        """
        execd_url = self._execd_url(sandbox_id, "/code")
        body: dict[str, Any] = {"code": code}
        if context_id:
            body["context"] = {"id": context_id, "language": language}
        else:
            body["context"] = {"language": language}

        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        result_data: dict[str, Any] = {}
        error_data: dict[str, Any] = {}
        exit_code = 0

        try:
            async with httpx.AsyncClient(timeout=timeout + 10) as client:
                async with client.stream(
                    "POST",
                    execd_url,
                    json=body,
                    headers=self._execd_headers(),
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        event = self._parse_sse_line(line)
                        if not event:
                            continue
                        etype = event.get("type", "")
                        if etype == "stdout":
                            stdout_parts.append(event.get("text", ""))
                        elif etype == "stderr":
                            stderr_parts.append(event.get("text", ""))
                        elif etype == "result":
                            result_data = event.get("results", {})
                        elif etype == "error":
                            error_data = event.get("error", {})
                            exit_code = 1
        except httpx.TimeoutException:
            return {"error": f"Execution timed out after {timeout}s", "exit_code": -1}
        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text}", "exit_code": -1}
        except Exception as exc:
            return {"error": str(exc), "exit_code": -1}

        result: dict[str, Any] = {
            "exit_code": exit_code,
            "stdout": "".join(stdout_parts),
            "stderr": "".join(stderr_parts),
        }
        if result_data:
            result["result"] = result_data
        if error_data:
            result["error"] = error_data
        return result

    async def create_code_context(
        self,
        sandbox_id: str,
        language: str = "python",
    ) -> str:
        """Create a stateful code execution context. Returns context_id."""
        execd_url = self._execd_url(sandbox_id, "/code/context")
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                execd_url,
                json={"language": language},
                headers=self._execd_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("id", "")

    async def delete_code_context(
        self,
        sandbox_id: str,
        context_id: str,
    ) -> None:
        """Delete a code execution context."""
        execd_url = self._execd_url(sandbox_id, f"/code/contexts/{context_id}")
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(
                execd_url,
                headers=self._execd_headers(),
            )
            # 200 or 404 are both acceptable
            if resp.status_code not in (200, 404):
                resp.raise_for_status()

    # ------------------------------------------------------------------
    # Command execution (compatible with SandboxManager.execute)
    # ------------------------------------------------------------------

    async def execute(
        self,
        sandbox_id: str,
        language: str,
        code: str,
        timeout: int = 30,
    ) -> dict[str, Any]:
        """Execute code inside a sandbox — drop-in replacement for SandboxManager.execute.

        Routes to code interpreter for 'python' or command execution for 'shell'.
        """
        if language == "python":
            return await self.execute_code(sandbox_id, "python", code, timeout=timeout)
        elif language == "shell":
            return await self.execute_command(sandbox_id, code, timeout=timeout)
        else:
            return {"error": f"Unsupported language: {language}"}

    async def execute_command(
        self,
        sandbox_id: str,
        command: str,
        cwd: str | None = None,
        background: bool = False,
        timeout: int = 30,
    ) -> dict[str, Any]:
        """Execute a shell command via the OpenSandbox command API.

        Returns {"exit_code": int, "stdout": str, "stderr": str}.
        """
        execd_url = self._execd_url(sandbox_id, "/command")
        body: dict[str, Any] = {
            "command": command,
            "background": background,
            "timeout": timeout * 1000,  # API expects milliseconds
        }
        if cwd:
            body["cwd"] = cwd

        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        exit_code = 0
        command_id = ""

        try:
            async with httpx.AsyncClient(timeout=timeout + 10) as client:
                async with client.stream(
                    "POST",
                    execd_url,
                    json=body,
                    headers=self._execd_headers(),
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        event = self._parse_sse_line(line)
                        if not event:
                            continue
                        etype = event.get("type", "")
                        if etype == "stdout":
                            stdout_parts.append(event.get("text", ""))
                        elif etype == "stderr":
                            stderr_parts.append(event.get("text", ""))
                        elif etype == "init":
                            command_id = event.get("text", "")
                        elif etype == "error":
                            exit_code = 1
                            stderr_parts.append(event.get("text", ""))
        except httpx.TimeoutException:
            return {"error": f"Execution timed out after {timeout}s", "exit_code": -1}
        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text}", "exit_code": -1}
        except Exception as exc:
            return {"error": str(exc), "exit_code": -1}

        result: dict[str, Any] = {
            "exit_code": exit_code,
            "stdout": "".join(stdout_parts),
            "stderr": "".join(stderr_parts),
        }
        if command_id:
            result["command_id"] = command_id
        return result

    async def execute_command_streaming(
        self,
        sandbox_id: str,
        command: str,
        cwd: str | None = None,
        timeout: int = 30,
    ) -> AsyncIterator[dict[str, Any]]:
        """Execute a command and yield SSE events as they arrive.

        Yields dicts with keys: type, text, timestamp, etc.
        """
        execd_url = self._execd_url(sandbox_id, "/command")
        body: dict[str, Any] = {
            "command": command,
            "background": False,
            "timeout": timeout * 1000,
        }
        if cwd:
            body["cwd"] = cwd

        async with httpx.AsyncClient(timeout=timeout + 10) as client:
            async with client.stream(
                "POST",
                execd_url,
                json=body,
                headers=self._execd_headers(),
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    event = self._parse_sse_line(line)
                    if event:
                        yield event

    async def get_command_status(
        self,
        sandbox_id: str,
        command_id: str,
    ) -> dict[str, Any]:
        """Get the status of a running/completed command."""
        execd_url = self._execd_url(sandbox_id, f"/command/status/{command_id}")
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(execd_url, headers=self._execd_headers())
            resp.raise_for_status()
            return resp.json()

    async def interrupt_command(
        self,
        sandbox_id: str,
        command_id: str,
    ) -> None:
        """Interrupt a running command."""
        execd_url = self._execd_url(sandbox_id, "/command")
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(
                execd_url,
                params={"id": command_id},
                headers=self._execd_headers(),
            )
            resp.raise_for_status()

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    async def upload_file(
        self,
        sandbox_id: str,
        path: str,
        content: bytes,
        mode: int = 644,
    ) -> None:
        """Upload a file to the sandbox."""
        execd_url = self._execd_url(sandbox_id, "/files/upload")
        metadata = json.dumps({"path": path, "mode": mode})

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                execd_url,
                files={
                    "metadata": (None, metadata, "application/json"),
                    "file": ("file", content, "application/octet-stream"),
                },
                headers={"X-EXECD-ACCESS-TOKEN": ""}
                if not self._sandboxes.get(sandbox_id, {}).get("access_token")
                else self._execd_headers(
                    self._sandboxes[sandbox_id].get("access_token", "")
                ),
            )
            resp.raise_for_status()

    async def download_file(
        self,
        sandbox_id: str,
        path: str,
    ) -> bytes:
        """Download a file from the sandbox."""
        execd_url = self._execd_url(sandbox_id, "/files/download")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                execd_url,
                params={"path": path},
                headers=self._execd_headers(),
            )
            resp.raise_for_status()
            return resp.content

    async def get_file_info(
        self,
        sandbox_id: str,
        paths: list[str],
    ) -> dict[str, Any]:
        """Get metadata for files in the sandbox."""
        execd_url = self._execd_url(sandbox_id, "/files/info")
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                execd_url,
                params={"path": paths},
                headers=self._execd_headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def search_files(
        self,
        sandbox_id: str,
        path: str,
        pattern: str = "**",
    ) -> list[dict[str, Any]]:
        """Search for files in the sandbox."""
        execd_url = self._execd_url(sandbox_id, "/files/search")
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                execd_url,
                params={"path": path, "pattern": pattern},
                headers=self._execd_headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def delete_files(
        self,
        sandbox_id: str,
        paths: list[str],
    ) -> None:
        """Delete files from the sandbox."""
        execd_url = self._execd_url(sandbox_id, "/files")
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(
                execd_url,
                params={"path": paths},
                headers=self._execd_headers(),
            )
            resp.raise_for_status()

    # ------------------------------------------------------------------
    # Sandbox lifecycle management
    # ------------------------------------------------------------------

    async def pause_sandbox(self, sandbox_id: str) -> None:
        """Pause a running sandbox."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                self._lifecycle_url(f"/sandboxes/{sandbox_id}/pause"),
                headers=self._lifecycle_headers(),
            )
            if resp.status_code not in (200, 202):
                raise RuntimeError(
                    f"Failed to pause sandbox {sandbox_id}: "
                    f"HTTP {resp.status_code} — {resp.text}"
                )

    async def resume_sandbox(self, sandbox_id: str) -> None:
        """Resume a paused sandbox."""
        await self._resume_sandbox(sandbox_id)

    async def renew_expiration(
        self,
        sandbox_id: str,
        expires_at: str,
    ) -> str:
        """Renew sandbox TTL. Returns the new expiration time."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                self._lifecycle_url(f"/sandboxes/{sandbox_id}/renew-expiration"),
                json={"expiresAt": expires_at},
                headers=self._lifecycle_headers(),
            )
            resp.raise_for_status()
            return resp.json().get("expiresAt", expires_at)

    async def destroy_agent_container(self, agent_id: str) -> bool:
        """Destroy the sandbox for an agent. Returns True if destroyed."""
        # Find sandbox by agent_id
        key = None
        sandbox_id = None
        for k, sid in list(self._agent_sandbox_map.items()):
            if k.endswith(agent_id):
                key = k
                sandbox_id = sid
                break

        if not sandbox_id:
            return False

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(
                self._lifecycle_url(f"/sandboxes/{sandbox_id}"),
                headers=self._lifecycle_headers(),
            )
            success = resp.status_code in (200, 204, 404)

        if key:
            await self._cleanup_tracking(key, sandbox_id)

        if success:
            logger.info("Destroyed OpenSandbox %s for agent %s", sandbox_id, agent_id)
        return success

    async def cleanup_idle(self, max_idle_seconds: int = 3600) -> int:
        """Stop sandboxes idle for longer than max_idle_seconds."""
        now = time.time()
        to_remove: list[tuple[str, str]] = []

        for sandbox_id, info in list(self._sandboxes.items()):
            if now - info.get("last_used", 0) > max_idle_seconds:
                to_remove.append((info.get("agent_key", ""), sandbox_id))

        for key, sandbox_id in to_remove:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.delete(
                        self._lifecycle_url(f"/sandboxes/{sandbox_id}"),
                        headers=self._lifecycle_headers(),
                    )
            except Exception as exc:
                logger.warning("Failed to cleanup sandbox %s: %s", sandbox_id, exc)
            await self._cleanup_tracking(key, sandbox_id)

        if to_remove:
            logger.info("Cleaned up %d idle OpenSandbox instances", len(to_remove))
        return len(to_remove)

    async def destroy_agent_data(self, agent_id: str) -> None:
        """Destroy sandbox and any persistent data for an agent."""
        await self.destroy_agent_container(agent_id)

    # ------------------------------------------------------------------
    # Health checks
    # ------------------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        """Check OpenSandbox server health."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.server_url}/health")
                return {
                    "status": "ok" if resp.status_code == 200 else "unhealthy",
                    "server_url": self.server_url,
                    "http_status": resp.status_code,
                }
        except Exception as exc:
            return {
                "status": "unreachable",
                "server_url": self.server_url,
                "error": str(exc),
            }

    async def sandbox_health(self, sandbox_id: str) -> dict[str, Any]:
        """Check health of a specific sandbox via execd ping."""
        try:
            execd_url = self._execd_url(sandbox_id, "/ping")
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(execd_url, headers=self._execd_headers())
                return {
                    "status": "ok" if resp.status_code == 200 else "unhealthy",
                    "sandbox_id": sandbox_id,
                }
        except Exception as exc:
            return {
                "status": "unhealthy",
                "sandbox_id": sandbox_id,
                "error": str(exc),
            }

    async def get_metrics(self, sandbox_id: str) -> dict[str, Any]:
        """Get system metrics from a sandbox."""
        execd_url = self._execd_url(sandbox_id, "/metrics")
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(execd_url, headers=self._execd_headers())
            resp.raise_for_status()
            return resp.json()

    # ------------------------------------------------------------------
    # SSE parsing helper
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_sse_line(line: str) -> dict[str, Any] | None:
        """Parse a single SSE data line into a dict."""
        line = line.strip()
        if not line or line.startswith(":"):
            return None
        if line.startswith("data: "):
            data_str = line[6:]
            try:
                return json.loads(data_str)
            except json.JSONDecodeError:
                return {"type": "raw", "text": data_str}
        return None


# Singleton
_opensandbox_adapter: OpenSandboxAdapter | None = None


def get_opensandbox_adapter() -> OpenSandboxAdapter:
    global _opensandbox_adapter
    if _opensandbox_adapter is None:
        _opensandbox_adapter = OpenSandboxAdapter()
    return _opensandbox_adapter
