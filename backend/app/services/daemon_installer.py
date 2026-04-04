"""Daemon Installer — SSHs into remote hosts and installs bond-host-daemon.

Design Doc 089: Phase 2 — Remote Host Daemon, Gap 1.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from pathlib import Path
from typing import Any, AsyncGenerator

logger = logging.getLogger("bond.services.daemon_installer")

_DAEMON_SRC = Path(__file__).resolve().parent.parent / "sandbox" / "bond_host_daemon.py"
_REQUIREMENTS_SRC = Path(__file__).resolve().parent.parent / "sandbox" / "requirements-daemon.txt"

_REMOTE_INSTALL_DIR = "/opt/bond"
_REMOTE_DAEMON_PATH = f"{_REMOTE_INSTALL_DIR}/bond_host_daemon.py"
_REMOTE_REQUIREMENTS_PATH = f"{_REMOTE_INSTALL_DIR}/requirements-daemon.txt"
_SERVICE_NAME = "bond-host-daemon"

_SSH_CONNECTION_ERRORS = ("Permission denied", "Connection refused", "No route to host",
                          "Connection timed out", "Could not resolve hostname",
                          "Network is unreachable", "Connection reset")


def _check_ssh_error(stderr: str) -> str | None:
    """If stderr indicates an SSH connection failure, return a user-friendly message."""
    for pattern in _SSH_CONNECTION_ERRORS:
        if pattern.lower() in stderr.lower():
            return f"SSH connection failed: {stderr.strip()}"
    return None


_SYSTEMD_UNIT_TEMPLATE = """\
[Unit]
Description=Bond Host Daemon
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 {daemon_path} --host 0.0.0.0 --port {daemon_port} --auth-token {auth_token}
Restart=always
RestartSec=5
Environment=BOND_DAEMON_AUTH_TOKEN={auth_token}

[Install]
WantedBy=multi-user.target
"""



def _resolve_ssh_key_path(ssh_key_path: str | None) -> str | None:
    """Resolve an SSH key path, probing system defaults if none provided."""
    if ssh_key_path:
        return ssh_key_path
    # Probe common system SSH key locations
    for candidate in (
        "/root/.ssh/id_ed25519",
        "/root/.ssh/id_rsa",
        "/root/.ssh/id_ecdsa",
    ):
        if Path(candidate).is_file():
            return candidate
    return None


async def _run_ssh_command(
    host: str,
    port: int,
    user: str,
    ssh_key_path: str | None,
    command: str,
    timeout: float = 30.0,
) -> tuple[int, str, str]:
    """Run a command on a remote host via SSH.

    If ssh_key_path is None or empty, probes for system SSH keys at
    common locations (/root/.ssh/id_ed25519, id_rsa, id_ecdsa).
    """
    resolved_key = _resolve_ssh_key_path(ssh_key_path)
    args = [
        "ssh",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
    ]
    if resolved_key:
        args.extend(["-i", resolved_key])
    args.extend([
        "-p", str(port),
        f"{user}@{host}",
        command,
    ])
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return -1, "", "SSH command timed out"
    return proc.returncode or 0, stdout.decode(), stderr.decode()


async def _scp_file(
    host: str,
    port: int,
    user: str,
    ssh_key_path: str | None,
    local_path: str | Path,
    remote_path: str,
    timeout: float = 30.0,
) -> bool:
    """Copy a file to a remote host via SCP.

    If ssh_key_path is None or empty, probes for system SSH keys at
    common locations (/root/.ssh/id_ed25519, id_rsa, id_ecdsa).
    """
    resolved_key = _resolve_ssh_key_path(ssh_key_path)
    args = [
        "scp",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
    ]
    if resolved_key:
        args.extend(["-i", resolved_key])
    args.extend([
        "-P", str(port),
        str(local_path),
        f"{user}@{host}:{remote_path}",
    ])
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        logger.error("SCP timed out copying %s to %s:%s", local_path, host, remote_path)
        return False
    if proc.returncode != 0:
        logger.error("SCP failed: %s", stderr.decode())
        return False
    return True


class DaemonInstaller:
    """Installs, uninstalls, and manages bond-host-daemon on remote hosts."""

    async def check_prerequisites(
        self,
        host: str,
        port: int,
        user: str,
        ssh_key_path: str | None,
    ) -> dict[str, Any]:
        """Check that Docker and Python 3.10+ are available on the remote host."""
        result: dict[str, Any] = {
            "docker": False,
            "python": False,
            "docker_version": "",
            "python_version": "",
            "errors": [],
        }

        # Check Docker
        rc, stdout, stderr = await _run_ssh_command(
            host, port, user, ssh_key_path, "docker --version"
        )
        if rc == 0:
            result["docker"] = True
            result["docker_version"] = stdout.strip()
        else:
            ssh_err = _check_ssh_error(stderr)
            if ssh_err:
                result["errors"].append(ssh_err)
                return result  # No point continuing if SSH itself failed
            result["errors"].append(f"Docker not found: {stderr.strip()}")

        # Check Docker is running
        if result["docker"]:
            rc, _, stderr = await _run_ssh_command(
                host, port, user, ssh_key_path, "docker info --format '{{.ServerVersion}}'"
            )
            if rc != 0:
                result["docker"] = False
                result["errors"].append(f"Docker not running: {stderr.strip()}")

        # Check Python 3.10+
        rc, stdout, stderr = await _run_ssh_command(
            host, port, user, ssh_key_path,
            "python3 -c \"import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')\"",
        )
        if rc == 0:
            version = stdout.strip()
            parts = version.split(".")
            if len(parts) >= 2 and (int(parts[0]) > 3 or (int(parts[0]) == 3 and int(parts[1]) >= 10)):
                result["python"] = True
                result["python_version"] = version
            else:
                result["errors"].append(f"Python 3.10+ required, found {version}")
        else:
            ssh_err = _check_ssh_error(stderr)
            if ssh_err:
                result["errors"].append(ssh_err)
            else:
                result["errors"].append(f"Python3 not found: {stderr.strip()}")

        return result

    async def install(
        self,
        host: str,
        port: int,
        user: str,
        ssh_key_path: str | None,
        daemon_port: int = 9100,
    ) -> dict[str, Any]:
        """Install bond-host-daemon on a remote host.

        Returns {success: bool, auth_token: str, errors: []}.
        """
        errors: list[str] = []

        # 1. Check prerequisites
        prereqs = await self.check_prerequisites(host, port, user, ssh_key_path)
        if not prereqs["docker"] or not prereqs["python"]:
            return {"success": False, "auth_token": "", "errors": prereqs["errors"]}

        # 2. Create install directory
        rc, _, stderr = await _run_ssh_command(
            host, port, user, ssh_key_path,
            f"sudo mkdir -p {_REMOTE_INSTALL_DIR} && sudo chown {user}:{user} {_REMOTE_INSTALL_DIR}",
        )
        if rc != 0:
            errors.append(f"Failed to create install dir: {stderr.strip()}")
            return {"success": False, "auth_token": "", "errors": errors}

        # 3. Copy daemon files
        if not await _scp_file(host, port, user, ssh_key_path, _DAEMON_SRC, _REMOTE_DAEMON_PATH):
            errors.append("Failed to copy bond_host_daemon.py")
            return {"success": False, "auth_token": "", "errors": errors}

        if not await _scp_file(host, port, user, ssh_key_path, _REQUIREMENTS_SRC, _REMOTE_REQUIREMENTS_PATH):
            errors.append("Failed to copy requirements-daemon.txt")
            return {"success": False, "auth_token": "", "errors": errors}

        # 4. Install Python dependencies
        rc, _, stderr = await _run_ssh_command(
            host, port, user, ssh_key_path,
            f"pip install -r {_REMOTE_REQUIREMENTS_PATH}",
            timeout=120.0,
        )
        if rc != 0:
            # Try pip3
            rc, _, stderr = await _run_ssh_command(
                host, port, user, ssh_key_path,
                f"pip3 install -r {_REMOTE_REQUIREMENTS_PATH}",
                timeout=120.0,
            )
            if rc != 0:
                errors.append(f"Failed to install dependencies: {stderr.strip()}")
                return {"success": False, "auth_token": "", "errors": errors}

        # 5. Generate auth token and install systemd service
        auth_token = secrets.token_urlsafe(32)
        unit_content = _SYSTEMD_UNIT_TEMPLATE.format(
            daemon_path=_REMOTE_DAEMON_PATH,
            daemon_port=daemon_port,
            auth_token=auth_token,
        )

        # Write systemd unit via SSH (escape for shell)
        escaped = unit_content.replace("'", "'\\''")
        rc, _, stderr = await _run_ssh_command(
            host, port, user, ssh_key_path,
            f"echo '{escaped}' | sudo tee /etc/systemd/system/{_SERVICE_NAME}.service > /dev/null",
        )
        if rc != 0:
            errors.append(f"Failed to write systemd unit: {stderr.strip()}")
            return {"success": False, "auth_token": "", "errors": errors}

        # 6. Enable and start service
        rc, _, stderr = await _run_ssh_command(
            host, port, user, ssh_key_path,
            f"sudo systemctl daemon-reload && sudo systemctl enable {_SERVICE_NAME} && sudo systemctl start {_SERVICE_NAME}",
        )
        if rc != 0:
            errors.append(f"Failed to start service: {stderr.strip()}")
            return {"success": False, "auth_token": "", "errors": errors}

        # 7. Wait for health check
        for attempt in range(10):
            await asyncio.sleep(1)
            rc, stdout, _ = await _run_ssh_command(
                host, port, user, ssh_key_path,
                f"curl -sf http://localhost:{daemon_port}/health",
            )
            if rc == 0 and "healthy" in stdout.lower() or "daemon_version" in stdout.lower():
                logger.info("Daemon installed and healthy on %s:%d", host, daemon_port)
                return {"success": True, "auth_token": auth_token, "errors": []}

        # Service started but health check didn't pass
        errors.append("Service started but health check did not pass within 10 seconds")
        return {"success": True, "auth_token": auth_token, "errors": errors}

    async def install_stream(
        self,
        host: str,
        port: int,
        user: str,
        ssh_key_path: str | None,
        daemon_port: int = 9100,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Install bond-host-daemon, yielding progress events as dicts.

        Each yielded dict has: {step, status, message}
        where status is 'running', 'ok', 'error', or 'done'.
        The final event with status='done' includes {success, auth_token, errors}.
        """

        def _evt(step: str, status: str, message: str, **extra: Any) -> dict[str, Any]:
            return {"step": step, "status": status, "message": message, **extra}

        # 1. Check prerequisites
        yield _evt("prerequisites", "running", f"Checking prerequisites on {host}...")

        prereqs = await self.check_prerequisites(host, port, user, ssh_key_path)

        # Check if the errors are actually SSH connection failures
        ssh_errors = [e for e in prereqs["errors"] if e.startswith("SSH connection failed")]
        if ssh_errors:
            for e in ssh_errors:
                yield _evt("prerequisites", "error", e)
            yield _evt("done", "done", "Installation failed: cannot connect to host",
                        success=False, auth_token="", errors=ssh_errors)
        else:
            if prereqs["docker"]:
                yield _evt("prerequisites", "ok", f"Docker: {prereqs['docker_version']}")
            else:
                for e in prereqs["errors"]:
                    if "docker" in e.lower() or "Docker" in e:
                        yield _evt("prerequisites", "error", e)

            if prereqs["python"]:
                yield _evt("prerequisites", "ok", f"Python: {prereqs['python_version']}")
            else:
                for e in prereqs["errors"]:
                    if "python" in e.lower() or "Python" in e:
                        yield _evt("prerequisites", "error", e)

        if ssh_errors or not prereqs["docker"] or not prereqs["python"]:
            if not ssh_errors:
                yield _evt("done", "done", "Installation failed: prerequisites not met",
                            success=False, auth_token="", errors=prereqs["errors"])
            return

        # 2. Create install directory
        yield _evt("mkdir", "running", f"Creating {_REMOTE_INSTALL_DIR}...")
        rc, _, stderr = await _run_ssh_command(
            host, port, user, ssh_key_path,
            f"sudo mkdir -p {_REMOTE_INSTALL_DIR} && sudo chown {user}:{user} {_REMOTE_INSTALL_DIR}",
        )
        if rc != 0:
            msg = f"Failed to create install dir: {stderr.strip()}"
            yield _evt("mkdir", "error", msg)
            yield _evt("done", "done", msg, success=False, auth_token="", errors=[msg])
            return
        yield _evt("mkdir", "ok", f"Created {_REMOTE_INSTALL_DIR}")

        # 3. Copy daemon files
        yield _evt("copy", "running", "Copying bond_host_daemon.py...")
        if not await _scp_file(host, port, user, ssh_key_path, _DAEMON_SRC, _REMOTE_DAEMON_PATH):
            msg = "Failed to copy bond_host_daemon.py"
            yield _evt("copy", "error", msg)
            yield _evt("done", "done", msg, success=False, auth_token="", errors=[msg])
            return
        yield _evt("copy", "ok", "Copied bond_host_daemon.py")

        yield _evt("copy", "running", "Copying requirements-daemon.txt...")
        if not await _scp_file(host, port, user, ssh_key_path, _REQUIREMENTS_SRC, _REMOTE_REQUIREMENTS_PATH):
            msg = "Failed to copy requirements-daemon.txt"
            yield _evt("copy", "error", msg)
            yield _evt("done", "done", msg, success=False, auth_token="", errors=[msg])
            return
        yield _evt("copy", "ok", "Copied requirements-daemon.txt")

        # 4. Install Python dependencies
        yield _evt("deps", "running", "Installing Python dependencies (this may take a minute)...")
        rc, _, stderr = await _run_ssh_command(
            host, port, user, ssh_key_path,
            f"pip install -r {_REMOTE_REQUIREMENTS_PATH}",
            timeout=120.0,
        )
        if rc != 0:
            yield _evt("deps", "running", "Retrying with pip3...")
            rc, _, stderr = await _run_ssh_command(
                host, port, user, ssh_key_path,
                f"pip3 install -r {_REMOTE_REQUIREMENTS_PATH}",
                timeout=120.0,
            )
            if rc != 0:
                msg = f"Failed to install dependencies: {stderr.strip()}"
                yield _evt("deps", "error", msg)
                yield _evt("done", "done", msg, success=False, auth_token="", errors=[msg])
                return
        yield _evt("deps", "ok", "Dependencies installed")

        # 5. Generate auth token and install systemd service
        yield _evt("systemd", "running", "Configuring systemd service...")
        auth_token = secrets.token_urlsafe(32)
        unit_content = _SYSTEMD_UNIT_TEMPLATE.format(
            daemon_path=_REMOTE_DAEMON_PATH,
            daemon_port=daemon_port,
            auth_token=auth_token,
        )
        escaped = unit_content.replace("'", "'\\''")
        rc, _, stderr = await _run_ssh_command(
            host, port, user, ssh_key_path,
            f"echo '{escaped}' | sudo tee /etc/systemd/system/{_SERVICE_NAME}.service > /dev/null",
        )
        if rc != 0:
            msg = f"Failed to write systemd unit: {stderr.strip()}"
            yield _evt("systemd", "error", msg)
            yield _evt("done", "done", msg, success=False, auth_token="", errors=[msg])
            return
        yield _evt("systemd", "ok", "Systemd unit installed")

        # 6. Enable and start service
        yield _evt("start", "running", "Starting bond-host-daemon service...")
        rc, _, stderr = await _run_ssh_command(
            host, port, user, ssh_key_path,
            f"sudo systemctl daemon-reload && sudo systemctl enable {_SERVICE_NAME} && sudo systemctl start {_SERVICE_NAME}",
        )
        if rc != 0:
            msg = f"Failed to start service: {stderr.strip()}"
            yield _evt("start", "error", msg)
            yield _evt("done", "done", msg, success=False, auth_token="", errors=[msg])
            return
        yield _evt("start", "ok", "Service started")

        # 7. Wait for health check
        yield _evt("health", "running", "Waiting for health check...")
        for attempt in range(10):
            await asyncio.sleep(1)
            rc, stdout, _ = await _run_ssh_command(
                host, port, user, ssh_key_path,
                f"curl -sf http://localhost:{daemon_port}/health",
            )
            if rc == 0 and ("healthy" in stdout.lower() or "daemon_version" in stdout.lower()):
                yield _evt("health", "ok", f"Health check passed (attempt {attempt + 1})")
                yield _evt("done", "done", "Daemon installed successfully",
                            success=True, auth_token=auth_token, errors=[])
                return
            yield _evt("health", "running", f"Health check attempt {attempt + 1}/10...")

        errors = ["Service started but health check did not pass within 10 seconds"]
        yield _evt("health", "error", errors[0])
        yield _evt("done", "done", "Daemon installed (health check warning)",
                    success=True, auth_token=auth_token, errors=errors)

    async def uninstall(
        self,
        host: str,
        port: int,
        user: str,
        ssh_key_path: str | None,
    ) -> dict[str, Any]:
        """Stop and remove bond-host-daemon from a remote host."""
        errors: list[str] = []

        # Stop and disable service
        rc, _, stderr = await _run_ssh_command(
            host, port, user, ssh_key_path,
            f"sudo systemctl stop {_SERVICE_NAME} 2>/dev/null; "
            f"sudo systemctl disable {_SERVICE_NAME} 2>/dev/null; "
            f"sudo rm -f /etc/systemd/system/{_SERVICE_NAME}.service; "
            f"sudo systemctl daemon-reload",
        )
        if rc != 0:
            errors.append(f"Service removal warning: {stderr.strip()}")

        # Remove installed files
        rc, _, stderr = await _run_ssh_command(
            host, port, user, ssh_key_path,
            f"rm -rf {_REMOTE_INSTALL_DIR}",
        )
        if rc != 0:
            errors.append(f"File cleanup warning: {stderr.strip()}")

        return {"success": True, "errors": errors}

    async def check_status(
        self,
        host: str,
        port: int,
        user: str,
        ssh_key_path: str | None,
    ) -> dict[str, Any]:
        """Check if bond-host-daemon is running on a remote host."""
        result: dict[str, Any] = {"running": False, "version": "", "uptime": ""}

        rc, stdout, _ = await _run_ssh_command(
            host, port, user, ssh_key_path,
            f"systemctl is-active {_SERVICE_NAME}",
        )
        if rc == 0 and stdout.strip() == "active":
            result["running"] = True

        # Get uptime
        rc, stdout, _ = await _run_ssh_command(
            host, port, user, ssh_key_path,
            f"systemctl show {_SERVICE_NAME} --property=ActiveEnterTimestamp --value",
        )
        if rc == 0:
            result["uptime"] = stdout.strip()

        return result
