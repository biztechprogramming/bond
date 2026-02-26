"""Tests for the agent worker (container-side FastAPI app) and native tools."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.worker import _AGENT_DB_SCHEMA, _state, app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def agent_db(tmp_path):
    """Provide a fresh agent.db with schema applied."""
    db_path = tmp_path / "agent.db"
    db = await aiosqlite.connect(str(db_path))
    await db.execute("PRAGMA journal_mode=WAL")
    await db.executescript(_AGENT_DB_SCHEMA)
    await db.commit()
    yield db
    await db.close()


@pytest.fixture()
async def worker_client(agent_db, tmp_path):
    """Provide an httpx AsyncClient wired to the worker app with state configured."""
    _state.agent_db = agent_db
    _state.agent_id = "test-agent"
    _state.start_time = 1000000.0
    _state.config = {
        "agent_id": "test-agent",
        "model": "anthropic/claude-sonnet-4-20250514",
        "system_prompt": "You are a test agent.",
        "tools": ["respond", "search_memory", "memory_save", "file_read", "file_write", "code_execute"],
        "max_iterations": 5,
    }
    _state.data_dir = tmp_path

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

    _state.agent_db = None


# ---------------------------------------------------------------------------
# /health endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_endpoint(worker_client):
    """GET /health returns status ok with agent_id and uptime."""
    resp = await worker_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["agent_id"] == "test-agent"
    assert isinstance(data["uptime"], (int, float))


# ---------------------------------------------------------------------------
# /interrupt endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interrupt_endpoint(worker_client):
    """POST /interrupt sets the interrupt event and stores messages."""
    resp = await worker_client.post(
        "/interrupt",
        json={"new_messages": [{"role": "user", "content": "Stop!"}]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["acknowledged"] is True
    assert len(_state.pending_messages) >= 1
    # Clean up
    _state.pending_messages.clear()
    _state.interrupt_event.clear()


# ---------------------------------------------------------------------------
# /turn endpoint (with mocked LLM)
# ---------------------------------------------------------------------------


def _make_llm_response(content=None, tool_calls=None):
    """Build a mock LLM response object (same pattern as test_agent_loop.py)."""
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls
    if tool_calls:
        message.model_dump.return_value = {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ],
        }
    else:
        message.model_dump.return_value = {
            "role": "assistant",
            "content": content,
        }
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


def _make_tool_call(call_id, name, arguments):
    tc = MagicMock()
    tc.id = call_id
    tc.function = MagicMock()
    tc.function.name = name
    tc.function.arguments = json.dumps(arguments)
    return tc


@pytest.mark.asyncio
async def test_turn_simple_text_response(worker_client):
    """POST /turn with LLM returning plain text => SSE stream with chunk + done."""
    text_response = _make_llm_response(content="Hello from worker!")

    with patch("backend.app.worker.litellm") as mock_mod:
        mock_mod.acompletion = AsyncMock(return_value=text_response)
        resp = await worker_client.post(
            "/turn",
            json={"message": "Hi", "history": [], "conversation_id": "conv-1"},
        )

    assert resp.status_code == 200
    body = resp.text
    assert "event: status" in body
    assert "event: chunk" in body
    assert "event: done" in body
    assert "Hello from worker!" in body


@pytest.mark.asyncio
async def test_turn_with_respond_tool(worker_client):
    """POST /turn where LLM calls the respond tool => terminal response."""
    tool_call = _make_tool_call("call_1", "respond", {"message": "Tool says hi!"})
    tool_response = _make_llm_response(tool_calls=[tool_call])

    with patch("backend.app.worker.litellm") as mock_mod:
        mock_mod.acompletion = AsyncMock(return_value=tool_response)
        resp = await worker_client.post(
            "/turn",
            json={"message": "Test", "history": [], "conversation_id": "conv-2"},
        )

    assert resp.status_code == 200
    assert "Tool says hi!" in resp.text


# ---------------------------------------------------------------------------
# Native file_read / file_write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_native_file_read(tmp_path):
    """Native file_read opens a real file."""
    from backend.app.agent.tools.native import handle_file_read

    test_file = tmp_path / "hello.txt"
    test_file.write_text("Hello, world!")

    result = await handle_file_read({"path": str(test_file)}, {})
    assert result["content"] == "Hello, world!"
    assert result["size"] == 13
    assert result["path"] == str(test_file)


@pytest.mark.asyncio
async def test_native_file_read_not_found():
    """Native file_read returns error for missing file."""
    from backend.app.agent.tools.native import handle_file_read

    result = await handle_file_read({"path": "/nonexistent/file.txt"}, {})
    assert "error" in result


@pytest.mark.asyncio
async def test_native_file_write(tmp_path):
    """Native file_write creates a file with content."""
    from backend.app.agent.tools.native import handle_file_write

    target = tmp_path / "subdir" / "output.txt"
    result = await handle_file_write(
        {"path": str(target), "content": "Written!"}, {}
    )
    assert result["status"] == "written"
    assert target.read_text() == "Written!"


@pytest.mark.asyncio
async def test_native_file_write_creates_parents(tmp_path):
    """Native file_write creates parent directories."""
    from backend.app.agent.tools.native import handle_file_write

    target = tmp_path / "a" / "b" / "c" / "deep.txt"
    result = await handle_file_write(
        {"path": str(target), "content": "Deep write"}, {}
    )
    assert result["status"] == "written"
    assert target.exists()


# ---------------------------------------------------------------------------
# Native code_execute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_native_code_execute_python(tmp_path, monkeypatch):
    """Native code_execute runs Python code."""
    from backend.app.agent.tools import native

    # Override the default /workspace cwd since it doesn't exist in tests
    monkeypatch.setattr(native, "_CODE_EXEC_CWD", str(tmp_path))

    result = await native.handle_code_execute(
        {"language": "python", "code": "print('hello')"}, {}
    )
    assert result["exit_code"] == 0
    assert "hello" in result["stdout"]


@pytest.mark.asyncio
async def test_native_code_execute_shell(tmp_path, monkeypatch):
    """Native code_execute runs shell commands."""
    from backend.app.agent.tools import native

    monkeypatch.setattr(native, "_CODE_EXEC_CWD", str(tmp_path))

    result = await native.handle_code_execute(
        {"language": "shell", "code": "echo world"}, {}
    )
    assert result["exit_code"] == 0
    assert "world" in result["stdout"]


@pytest.mark.asyncio
async def test_native_code_execute_error(tmp_path, monkeypatch):
    """Native code_execute captures stderr and non-zero exit code."""
    from backend.app.agent.tools import native

    monkeypatch.setattr(native, "_CODE_EXEC_CWD", str(tmp_path))

    result = await native.handle_code_execute(
        {"language": "python", "code": "import sys; sys.exit(42)"}, {}
    )
    assert result["exit_code"] == 42


@pytest.mark.asyncio
async def test_native_code_execute_unsupported_language():
    """Native code_execute rejects unsupported languages."""
    from backend.app.agent.tools.native import handle_code_execute

    result = await handle_code_execute(
        {"language": "ruby", "code": "puts 'hi'"}, {}
    )
    assert "error" in result


# ---------------------------------------------------------------------------
# Native memory_save with promotion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_native_memory_save(agent_db):
    """Native memory_save writes to local DB."""
    from backend.app.agent.tools.native import handle_memory_save

    result = await handle_memory_save(
        {"content": "Test memory", "memory_type": "general"},
        {"agent_db": agent_db},
    )
    assert result["status"] == "saved"
    assert "memory_id" in result
    assert "_promote" not in result  # general type is not promoted


@pytest.mark.asyncio
async def test_native_memory_save_promoted(agent_db):
    """Native memory_save flags promotable types with _promote dict."""
    from backend.app.agent.tools.native import handle_memory_save

    result = await handle_memory_save(
        {"content": "User prefers dark mode", "memory_type": "preference"},
        {"agent_db": agent_db},
    )
    assert result["status"] == "saved"
    assert "_promote" in result
    assert result["_promote"]["type"] == "preference"
    assert result["_promote"]["content"] == "User prefers dark mode"


@pytest.mark.asyncio
async def test_native_memory_save_fact_promoted(agent_db):
    """Facts are promotable."""
    from backend.app.agent.tools.native import handle_memory_save

    result = await handle_memory_save(
        {"content": "User is in EST timezone", "memory_type": "fact"},
        {"agent_db": agent_db},
    )
    assert "_promote" in result
    assert result["_promote"]["type"] == "fact"


# ---------------------------------------------------------------------------
# Native search_memory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_native_search_memory(agent_db):
    """Native search_memory finds saved memories via FTS."""
    from backend.app.agent.tools.native import handle_memory_save, handle_search_memory

    # Save a memory first
    await handle_memory_save(
        {"content": "The project uses FastAPI and SQLite", "memory_type": "fact"},
        {"agent_db": agent_db},
    )

    # Search for it
    result = await handle_search_memory(
        {"query": "FastAPI SQLite"},
        {"agent_db": agent_db},
    )
    assert result["count"] >= 1
    assert any("FastAPI" in r["content"] for r in result["results"])


@pytest.mark.asyncio
async def test_native_search_memory_no_db():
    """Search returns error when no agent_db in context."""
    from backend.app.agent.tools.native import handle_search_memory

    result = await handle_search_memory({"query": "test"}, {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Native respond tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_native_respond():
    """Native respond tool returns terminal flag."""
    from backend.app.agent.tools.native import handle_respond

    result = await handle_respond({"message": "Done!"}, {})
    assert result["message"] == "Done!"
    assert result["_terminal"] is True


# ---------------------------------------------------------------------------
# Native registry
# ---------------------------------------------------------------------------


def test_native_registry_has_expected_tools():
    """Native registry includes all expected tools."""
    from backend.app.agent.tools.native_registry import build_native_registry

    registry = build_native_registry()
    expected = {
        "respond", "search_memory", "memory_save",
        "memory_update", "memory_delete",
        "code_execute", "file_read", "file_write",
        "web_search", "web_read",
    }
    assert set(registry.registered_names) == expected
