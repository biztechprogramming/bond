"""FaucetManager — manages the Faucet database gateway lifecycle (Design Doc 107)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

import httpx

from backend.app.config import get_settings

logger = logging.getLogger("bond.faucet")

FAUCET_PORT = 18795


class FaucetManager:
    """Singleton manager for the Faucet database gateway process."""

    def __init__(self) -> None:
        settings = get_settings()
        self.faucet_bin = Path(getattr(settings, "faucet_bin", str(settings.bond_home / "bin" / "faucet")))
        self.faucet_config_dir = Path(getattr(settings, "faucet_config_dir", str(settings.bond_home / "faucet")))
        self.port = getattr(settings, "faucet_port", FAUCET_PORT)
        self._process: Optional[asyncio.subprocess.Process] = None

    async def ensure_installed(self) -> bool:
        """Check if the Faucet binary exists. Returns True if installed."""
        if self.faucet_bin.exists():
            return True
        logger.warning("Faucet binary not found at %s — install it to enable database integration", self.faucet_bin)
        return False

    async def start(self) -> None:
        """Start the Faucet process."""
        if not await self.ensure_installed():
            raise RuntimeError(f"Faucet binary not found at {self.faucet_bin}")

        if self._process and self._process.returncode is None:
            logger.info("Faucet already running (pid %s)", self._process.pid)
            return

        self.faucet_config_dir.mkdir(parents=True, exist_ok=True)

        self._process = await asyncio.create_subprocess_exec(
            str(self.faucet_bin),
            "serve",
            "--config-dir", str(self.faucet_config_dir),
            "--port", str(self.port),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        logger.info("Faucet started (pid %s) on port %s", self._process.pid, self.port)

    async def stop(self) -> None:
        """Gracefully stop the Faucet process."""
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=10)
            except asyncio.TimeoutError:
                self._process.kill()
            logger.info("Faucet stopped")
        self._process = None

    async def health_check(self) -> dict:
        """Check Faucet health via HTTP."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"http://localhost:{self.port}/api/v1/system/health")
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def _run_cli(self, *args: str) -> tuple[str, str, int]:
        """Run a Faucet CLI command and return (stdout, stderr, returncode)."""
        if not await self.ensure_installed():
            raise RuntimeError(f"Faucet binary not found at {self.faucet_bin}")

        proc = await asyncio.create_subprocess_exec(
            str(self.faucet_bin),
            *args,
            "--config-dir", str(self.faucet_config_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        return stdout.decode(), stderr.decode(), proc.returncode or 0

    async def add_database(self, name: str, driver: str, dsn: str) -> dict:
        """Register a database with Faucet via CLI."""
        stdout, stderr, rc = await self._run_cli("db", "add", name, "--driver", driver, "--dsn", dsn)
        if rc != 0:
            raise RuntimeError(f"faucet db add failed: {stderr}")
        return {"status": "ok", "output": stdout}

    async def remove_database(self, name: str) -> dict:
        """Remove a database from Faucet via CLI."""
        stdout, stderr, rc = await self._run_cli("db", "remove", name)
        if rc != 0:
            raise RuntimeError(f"faucet db remove failed: {stderr}")
        return {"status": "ok", "output": stdout}

    async def create_role(self, name: str, permissions: list[str]) -> dict:
        """Create a Faucet role via CLI."""
        cmd = ["role", "create", name]
        for perm in permissions:
            cmd.extend(["--permission", perm])
        stdout, stderr, rc = await self._run_cli(*cmd)
        if rc != 0:
            raise RuntimeError(f"faucet role create failed: {stderr}")
        return {"status": "ok", "output": stdout}

    async def create_api_key(self, role_name: str, key_name: str) -> dict:
        """Create a Faucet API key via CLI. Returns the key value."""
        stdout, stderr, rc = await self._run_cli("key", "create", key_name, "--role", role_name)
        if rc != 0:
            raise RuntimeError(f"faucet key create failed: {stderr}")
        return {"status": "ok", "key": stdout.strip()}

    async def delete_api_key(self, key_name: str) -> dict:
        """Delete a Faucet API key via CLI."""
        stdout, stderr, rc = await self._run_cli("key", "delete", key_name)
        if rc != 0:
            raise RuntimeError(f"faucet key delete failed: {stderr}")
        return {"status": "ok", "output": stdout}


# Singleton instance
faucet_manager = FaucetManager()
