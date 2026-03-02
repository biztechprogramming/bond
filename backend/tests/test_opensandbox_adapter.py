"""Tests for OpenSandboxAdapter — all HTTP calls are mocked."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.app.sandbox.opensandbox_adapter import OpenSandboxAdapter


# ── Helpers ──


def _mock_response(status_code: int = 200, json_data: dict | list | None = None, text: str = "", content: bytes = b""):
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text or json.dumps(json_data or {})
    resp.content = content or resp.text.encode()
    resp.json.return_value = json_data or {}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    return resp


def _make_adapter(server_url: str = "http://test-server:8090") -> OpenSandboxAdapter:
    adapter = OpenSandboxAdapter(server_url=server_url, api_key="test-key")
    return adapter


def _seed_sandbox(adapter: OpenSandboxAdapter, sandbox_id: str = "sb-123", agent_key: str = "bond-agent-test-id"):
    """Pre-seed adapter tracking as if a sandbox was already created."""
    adapter._sandboxes[sandbox_id] = {
        "sandbox_id": sandbox_id,
        "agent_key": agent_key,
        "execd_url": "http://execd-host:44772",
        "last_used": 0,
    }
    adapter._agent_sandbox_map[agent_key] = sandbox_id


# ── Lifecycle tests ──


@pytest.mark.asyncio
async def test_create_sandbox_success():
    """Should create a sandbox and wait for Running state."""
    adapter = _make_adapter()

    async def mock_post(url, **kwargs):
        if "/sandboxes" in url and "endpoints" not in url and "pause" not in url and "resume" not in url and "renew" not in url:
            return _mock_response(202, {"id": "sb-new", "status": {"state": "Pending"}})
        return _mock_response(200, {})

    async def mock_get(url, **kwargs):
        if "endpoints" in url:
            return _mock_response(200, {"endpoint": "http://execd:44772"})
        if "/sandboxes/sb-new" in url:
            return _mock_response(200, {"status": {"state": "Running"}})
        return _mock_response(200, {})

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.post = AsyncMock(side_effect=mock_post)
        client_instance.get = AsyncMock(side_effect=mock_get)
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = client_instance

        agent = {
            "id": "test-id",
            "name": "TestAgent",
            "sandbox_image": "python:3.12-slim",
            "workspace_mounts": [],
        }
        result = await adapter.ensure_running(agent)
        assert result["sandbox_id"] == "sb-new"
        assert "sb-new" in adapter._sandboxes


@pytest.mark.asyncio
async def test_ensure_running_reuses_existing():
    """Should reuse a running sandbox instead of creating a new one."""
    adapter = _make_adapter()
    _seed_sandbox(adapter, "sb-existing", "bond-testagent-test-id")

    async def mock_get(url, **kwargs):
        return _mock_response(200, {"status": {"state": "Running"}})

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.get = AsyncMock(side_effect=mock_get)
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = client_instance

        agent = {"id": "test-id", "name": "TestAgent"}
        result = await adapter.ensure_running(agent)
        assert result["sandbox_id"] == "sb-existing"


@pytest.mark.asyncio
async def test_destroy_agent_container():
    """Should delete sandbox via lifecycle API."""
    adapter = _make_adapter()
    _seed_sandbox(adapter, "sb-del", "bond-agent-del-id")

    async def mock_delete(url, **kwargs):
        return _mock_response(204)

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.delete = AsyncMock(side_effect=mock_delete)
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = client_instance

        result = await adapter.destroy_agent_container("del-id")
        assert result is True
        assert "sb-del" not in adapter._sandboxes


@pytest.mark.asyncio
async def test_cleanup_idle():
    """Should clean up sandboxes that haven't been used recently."""
    adapter = _make_adapter()
    _seed_sandbox(adapter, "sb-old", "bond-agent-old-id")
    adapter._sandboxes["sb-old"]["last_used"] = 0  # Very old

    async def mock_delete(url, **kwargs):
        return _mock_response(204)

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.delete = AsyncMock(side_effect=mock_delete)
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = client_instance

        count = await adapter.cleanup_idle(max_idle_seconds=1)
        assert count == 1
        assert "sb-old" not in adapter._sandboxes


# ── Code execution tests ──


@pytest.mark.asyncio
async def test_execute_code_python():
    """Should execute Python code via code interpreter API."""
    adapter = _make_adapter()
    _seed_sandbox(adapter)

    sse_lines = [
        'data: {"type": "stdout", "text": "hello\\n"}',
        'data: {"type": "result", "results": {"text/plain": "42"}}',
        'data: {"type": "execution_complete"}',
    ]

    async def mock_stream_lines():
        for line in sse_lines:
            yield line

    mock_response = AsyncMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.aiter_lines = mock_stream_lines
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.stream = MagicMock(return_value=mock_response)
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = client_instance

        result = await adapter.execute_code("sb-123", "python", "print('hello')")
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]
        assert result["result"]["text/plain"] == "42"


@pytest.mark.asyncio
async def test_execute_code_error():
    """Should handle code execution errors."""
    adapter = _make_adapter()
    _seed_sandbox(adapter)

    sse_lines = [
        'data: {"type": "error", "error": {"ename": "NameError", "evalue": "name x is not defined"}}',
    ]

    async def mock_stream_lines():
        for line in sse_lines:
            yield line

    mock_response = AsyncMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.aiter_lines = mock_stream_lines
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.stream = MagicMock(return_value=mock_response)
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = client_instance

        result = await adapter.execute_code("sb-123", "python", "print(x)")
        assert result["exit_code"] == 1
        assert "error" in result


# ── Command execution tests ──


@pytest.mark.asyncio
async def test_execute_command():
    """Should execute shell command and collect stdout/stderr."""
    adapter = _make_adapter()
    _seed_sandbox(adapter)

    sse_lines = [
        'data: {"type": "init", "text": "cmd-001"}',
        'data: {"type": "stdout", "text": "file1.txt\\n"}',
        'data: {"type": "stdout", "text": "file2.txt\\n"}',
        'data: {"type": "execution_complete"}',
    ]

    async def mock_stream_lines():
        for line in sse_lines:
            yield line

    mock_response = AsyncMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.aiter_lines = mock_stream_lines
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.stream = MagicMock(return_value=mock_response)
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = client_instance

        result = await adapter.execute_command("sb-123", "ls -la")
        assert result["exit_code"] == 0
        assert "file1.txt" in result["stdout"]
        assert result["command_id"] == "cmd-001"


@pytest.mark.asyncio
async def test_execute_shell_via_legacy_interface():
    """Should route 'shell' language to execute_command via legacy execute()."""
    adapter = _make_adapter()
    _seed_sandbox(adapter)

    sse_lines = [
        'data: {"type": "stdout", "text": "hello\\n"}',
    ]

    async def mock_stream_lines():
        for line in sse_lines:
            yield line

    mock_response = AsyncMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.aiter_lines = mock_stream_lines
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.stream = MagicMock(return_value=mock_response)
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = client_instance

        result = await adapter.execute("sb-123", "shell", "echo hello")
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]


@pytest.mark.asyncio
async def test_execute_unsupported_language():
    """Should return error for unsupported language."""
    adapter = _make_adapter()
    _seed_sandbox(adapter)

    result = await adapter.execute("sb-123", "ruby", "puts 'hi'")
    assert "error" in result


# ── Command status and interrupt tests ──


@pytest.mark.asyncio
async def test_get_command_status():
    """Should return command status from execd API."""
    adapter = _make_adapter()
    _seed_sandbox(adapter)

    status_data = {
        "id": "cmd-001",
        "running": False,
        "exit_code": 0,
        "content": "ls -la",
    }

    async def mock_get(url, **kwargs):
        return _mock_response(200, status_data)

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.get = AsyncMock(side_effect=mock_get)
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = client_instance

        result = await adapter.get_command_status("sb-123", "cmd-001")
        assert result["exit_code"] == 0
        assert result["running"] is False


@pytest.mark.asyncio
async def test_interrupt_command():
    """Should send interrupt request to execd."""
    adapter = _make_adapter()
    _seed_sandbox(adapter)

    async def mock_delete(url, **kwargs):
        return _mock_response(200)

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.delete = AsyncMock(side_effect=mock_delete)
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = client_instance

        await adapter.interrupt_command("sb-123", "cmd-001")
        # No exception = success


# ── File operations tests ──


@pytest.mark.asyncio
async def test_download_file():
    """Should download file content from sandbox."""
    adapter = _make_adapter()
    _seed_sandbox(adapter)

    async def mock_get(url, **kwargs):
        return _mock_response(200, content=b"file content here")

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.get = AsyncMock(side_effect=mock_get)
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = client_instance

        content = await adapter.download_file("sb-123", "/workspace/test.txt")
        assert content == b"file content here"


@pytest.mark.asyncio
async def test_get_file_info():
    """Should return file metadata."""
    adapter = _make_adapter()
    _seed_sandbox(adapter)

    file_info = {
        "/workspace/test.txt": {
            "path": "/workspace/test.txt",
            "size": 100,
            "mode": 644,
        }
    }

    async def mock_get(url, **kwargs):
        return _mock_response(200, file_info)

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.get = AsyncMock(side_effect=mock_get)
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = client_instance

        result = await adapter.get_file_info("sb-123", ["/workspace/test.txt"])
        assert "/workspace/test.txt" in result


@pytest.mark.asyncio
async def test_search_files():
    """Should search files in sandbox."""
    adapter = _make_adapter()
    _seed_sandbox(adapter)

    files = [{"path": "/workspace/a.py", "size": 50, "mode": 644}]

    async def mock_get(url, **kwargs):
        return _mock_response(200, json_data=files)

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.get = AsyncMock(side_effect=mock_get)
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = client_instance

        result = await adapter.search_files("sb-123", "/workspace", "*.py")
        assert len(result) == 1
        assert result[0]["path"] == "/workspace/a.py"


@pytest.mark.asyncio
async def test_delete_files():
    """Should delete files from sandbox."""
    adapter = _make_adapter()
    _seed_sandbox(adapter)

    async def mock_delete(url, **kwargs):
        return _mock_response(200)

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.delete = AsyncMock(side_effect=mock_delete)
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = client_instance

        await adapter.delete_files("sb-123", ["/workspace/temp.txt"])
        # No exception = success


# ── Sandbox lifecycle tests ──


@pytest.mark.asyncio
async def test_pause_sandbox():
    """Should pause a running sandbox."""
    adapter = _make_adapter()
    _seed_sandbox(adapter)

    async def mock_post(url, **kwargs):
        return _mock_response(202)

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.post = AsyncMock(side_effect=mock_post)
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = client_instance

        await adapter.pause_sandbox("sb-123")
        # No exception = success


@pytest.mark.asyncio
async def test_renew_expiration():
    """Should renew sandbox TTL."""
    adapter = _make_adapter()
    _seed_sandbox(adapter)

    async def mock_post(url, **kwargs):
        return _mock_response(200, {"expiresAt": "2026-12-01T00:00:00Z"})

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.post = AsyncMock(side_effect=mock_post)
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = client_instance

        new_exp = await adapter.renew_expiration("sb-123", "2026-12-01T00:00:00Z")
        assert new_exp == "2026-12-01T00:00:00Z"


# ── Health check tests ──


@pytest.mark.asyncio
async def test_health_check_ok():
    """Should return ok when server is healthy."""
    adapter = _make_adapter()

    async def mock_get(url, **kwargs):
        return _mock_response(200)

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.get = AsyncMock(side_effect=mock_get)
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = client_instance

        result = await adapter.health_check()
        assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_health_check_unreachable():
    """Should return unreachable when server is down."""
    adapter = _make_adapter()

    async def mock_get(url, **kwargs):
        raise httpx.ConnectError("Connection refused")

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.get = AsyncMock(side_effect=mock_get)
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = client_instance

        result = await adapter.health_check()
        assert result["status"] == "unreachable"


@pytest.mark.asyncio
async def test_sandbox_health():
    """Should check execd health via /ping."""
    adapter = _make_adapter()
    _seed_sandbox(adapter)

    async def mock_get(url, **kwargs):
        return _mock_response(200)

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.get = AsyncMock(side_effect=mock_get)
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = client_instance

        result = await adapter.sandbox_health("sb-123")
        assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_get_metrics():
    """Should return system metrics from sandbox."""
    adapter = _make_adapter()
    _seed_sandbox(adapter)

    metrics = {
        "cpu_count": 4.0,
        "cpu_used_pct": 25.5,
        "mem_total_mib": 8192.0,
        "mem_used_mib": 2048.0,
        "timestamp": 1700000000000,
    }

    async def mock_get(url, **kwargs):
        return _mock_response(200, metrics)

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.get = AsyncMock(side_effect=mock_get)
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = client_instance

        result = await adapter.get_metrics("sb-123")
        assert result["cpu_count"] == 4.0
        assert result["mem_used_mib"] == 2048.0


# ── SSE parser tests ──


def test_parse_sse_line_valid():
    """Should parse valid SSE data lines."""
    result = OpenSandboxAdapter._parse_sse_line('data: {"type": "stdout", "text": "hello"}')
    assert result["type"] == "stdout"
    assert result["text"] == "hello"


def test_parse_sse_line_comment():
    """Should ignore SSE comment lines."""
    assert OpenSandboxAdapter._parse_sse_line(": this is a comment") is None


def test_parse_sse_line_empty():
    """Should return None for empty lines."""
    assert OpenSandboxAdapter._parse_sse_line("") is None
    assert OpenSandboxAdapter._parse_sse_line("   ") is None


def test_parse_sse_line_invalid_json():
    """Should handle non-JSON data gracefully."""
    result = OpenSandboxAdapter._parse_sse_line("data: not json")
    assert result["type"] == "raw"
    assert result["text"] == "not json"


# ── Config selection test ──


def test_get_sandbox_backend_default():
    """Should default to 'legacy' backend."""
    from backend.app.sandbox import get_sandbox_backend

    # With default config (no sandbox_backend key), should return 'legacy'
    backend = get_sandbox_backend()
    assert backend == "legacy"


def test_get_executor_legacy():
    """Should return SandboxManager for legacy backend."""
    from backend.app.sandbox import get_executor
    from backend.app.sandbox.manager import SandboxManager

    with patch("backend.app.sandbox.get_sandbox_backend", return_value="legacy"):
        executor = get_executor()
        assert isinstance(executor, SandboxManager)


def test_get_executor_opensandbox():
    """Should return OpenSandboxAdapter for opensandbox backend."""
    from backend.app.sandbox import get_executor

    with patch("backend.app.sandbox.get_sandbox_backend", return_value="opensandbox"):
        executor = get_executor()
        assert isinstance(executor, OpenSandboxAdapter)


# ── Code context management tests ──


@pytest.mark.asyncio
async def test_create_code_context():
    """Should create a code execution context and return its ID."""
    adapter = _make_adapter()
    _seed_sandbox(adapter)

    async def mock_post(url, **kwargs):
        return _mock_response(200, {"id": "ctx-abc", "language": "python"})

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.post = AsyncMock(side_effect=mock_post)
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = client_instance

        ctx_id = await adapter.create_code_context("sb-123", "python")
        assert ctx_id == "ctx-abc"


@pytest.mark.asyncio
async def test_delete_code_context():
    """Should delete a code execution context."""
    adapter = _make_adapter()
    _seed_sandbox(adapter)

    async def mock_delete(url, **kwargs):
        return _mock_response(200)

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.delete = AsyncMock(side_effect=mock_delete)
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = client_instance

        await adapter.delete_code_context("sb-123", "ctx-abc")
        # No exception = success


@pytest.mark.asyncio
async def test_get_or_create_container_compat():
    """Should work as compatibility shim returning sandbox_id."""
    adapter = _make_adapter()

    # Mock the full ensure_running flow
    async def mock_ensure(agent):
        return {"sandbox_id": "sb-compat", "worker_url": "http://test"}

    adapter.ensure_running = AsyncMock(side_effect=mock_ensure)

    result = await adapter.get_or_create_container(
        "agent-1", "python:3.12-slim", agent_name="test"
    )
    assert result == "sb-compat"
