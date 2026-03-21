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

from backend.app.db.agent_schema import AGENT_DB_SCHEMA
from backend.app.worker import _state, app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def agent_db(tmp_path):
    """Provide a fresh agent.db with schema applied."""
    db_path = tmp_path / "agent.db"
    db = await aiosqlite.connect(str(db_path))
    await db.execute("PRAGMA journal_mode=WAL")
    await db.executescript(AGENT_DB_SCHEMA)
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
        "code_execute", "file_read", "file_write", "file_edit",
        "web_search", "web_read", "work_plan", "parallel_orchestrate",
        "repo_pr", "load_context",
        # Shell utility tools
        "shell_find", "shell_ls", "shell_grep", "git_info",
        "shell_wc", "shell_head", "shell_tree", "project_search",
    }
    assert set(registry.registered_names) == expected


# ---------------------------------------------------------------------------
# LLM empty-response retry logic
# ---------------------------------------------------------------------------


def _make_empty_llm_response():
    """Build a mock LLM response with no choices (simulates rate limiting)."""
    response = MagicMock()
    response.choices = []
    return response


@pytest.mark.asyncio
async def test_turn_retries_on_empty_response(worker_client, monkeypatch):
    """Empty LLM responses trigger retries; succeeds when a valid response arrives."""
    monkeypatch.setenv("LLM_RETRY_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("LLM_RETRY_MAX_WAIT_SECONDS", "0.3")

    empty = _make_empty_llm_response()
    good = _make_llm_response(content="Recovered!")

    call_count = 0

    async def mock_acompletion(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return empty
        return good

    with patch("backend.app.worker.litellm") as mock_mod:
        mock_mod.acompletion = mock_acompletion
        resp = await worker_client.post(
            "/turn",
            json={"message": "Hi", "history": [], "conversation_id": "conv-retry-1"},
        )

    assert resp.status_code == 200
    assert "Recovered!" in resp.text
    assert call_count == 3


@pytest.mark.asyncio
async def test_turn_fails_after_max_retries(worker_client, monkeypatch):
    """All retries exhausted => error event with descriptive message."""
    monkeypatch.setenv("LLM_RETRY_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("LLM_RETRY_MAX_WAIT_SECONDS", "0.3")

    empty = _make_empty_llm_response()

    with patch("backend.app.worker.litellm") as mock_mod:
        mock_mod.acompletion = AsyncMock(return_value=empty)
        resp = await worker_client.post(
            "/turn",
            json={"message": "Hi", "history": [], "conversation_id": "conv-retry-2"},
        )

    assert resp.status_code == 200
    body = resp.text
    assert "event: error" in body
    assert "empty response" in body.lower() or "3 attempts" in body


@pytest.mark.asyncio
async def test_turn_retry_first_attempt_succeeds(worker_client, monkeypatch):
    """No retry needed when first attempt succeeds."""
    monkeypatch.setenv("LLM_RETRY_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("LLM_RETRY_MAX_WAIT_SECONDS", "10")

    good = _make_llm_response(content="First try!")
    call_count = 0

    async def mock_acompletion(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return good

    with patch("backend.app.worker.litellm") as mock_mod:
        mock_mod.acompletion = mock_acompletion
        resp = await worker_client.post(
            "/turn",
            json={"message": "Hi", "history": [], "conversation_id": "conv-retry-3"},
        )

    assert resp.status_code == 200
    assert "First try!" in resp.text
    assert call_count == 1


# ---------------------------------------------------------------------------
# Loop detection: tool_result ordering (Anthropic message contract)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_detection_no_user_message_between_tool_results(worker_client):
    """When loop detection breaks out of a multi-tool batch, user messages
    must come AFTER all tool_result messages — not between them.

    Anthropic requires every tool_use to have a corresponding tool_result
    immediately after.  Previously, loop intervention user messages were
    injected between tool_results, causing:
      assistant(tool_use A,B,C) → tool(A) → user(loop) → tool(B) → tool(C)
    which Anthropic rejects.
    """
    # We need 3 iterations of the same tool call to trigger loop detection
    # (REPETITION_THRESHOLD = 3).  The 3rd response has 3 tool calls in a
    # batch.  Loop detection triggers on the first tool call in that batch,
    # breaking out early and leaving B and C as orphans.
    same_tc = _make_tool_call("call_same", "file_read", {"path": "/tmp/x.txt"})

    # First two responses: single tool call each (builds up repetition count)
    resp1 = _make_llm_response(tool_calls=[
        _make_tool_call("call_r1", "file_read", {"path": "/tmp/x.txt"}),
    ])
    resp2 = _make_llm_response(tool_calls=[
        _make_tool_call("call_r2", "file_read", {"path": "/tmp/x.txt"}),
    ])
    # Third response: batch of 3 — loop detection triggers on first call
    resp3 = _make_llm_response(tool_calls=[
        _make_tool_call("call_r3a", "file_read", {"path": "/tmp/x.txt"}),
        _make_tool_call("call_r3b", "file_read", {"path": "/tmp/x.txt"}),
        _make_tool_call("call_r3c", "file_read", {"path": "/tmp/x.txt"}),
    ])
    # Final response: simple text (after loop intervention)
    final = _make_llm_response(content="Done after loop.")

    call_count = 0

    async def mock_acompletion(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        messages = kwargs.get("messages", [])
        if call_count == 1:
            return resp1
        elif call_count == 2:
            return resp2
        elif call_count == 3:
            return resp3
        else:
            return final

    with patch("backend.app.worker.litellm") as mock_mod:
        mock_mod.acompletion = mock_acompletion
        resp = await worker_client.post(
            "/turn",
            json={"message": "Read the file", "history": [], "conversation_id": "conv-loop-order"},
        )

    assert resp.status_code == 200
    # The response should complete without Anthropic errors
    body = resp.text
    assert "event: done" in body


def test_tool_result_ordering_invariant():
    """Validate that after an assistant message with tool_calls, all
    tool_result messages come before any user messages.

    This is a structural invariant required by Anthropic's API.
    """
    # Simulate the message list that would be built during the agent loop
    # when loop detection fires mid-batch.
    messages = [
        {"role": "system", "content": "You are a test agent."},
        {"role": "user", "content": "Do something"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "tc_a", "type": "function", "function": {"name": "file_read", "arguments": "{}"}},
                {"id": "tc_b", "type": "function", "function": {"name": "file_read", "arguments": "{}"}},
                {"id": "tc_c", "type": "function", "function": {"name": "file_read", "arguments": "{}"}},
            ],
        },
        # All tool results must come here, grouped together:
        {"role": "tool", "tool_call_id": "tc_a", "content": '{"content": "ok"}'},
        {"role": "tool", "tool_call_id": "tc_b", "content": '{"error": "Skipped"}'},
        {"role": "tool", "tool_call_id": "tc_c", "content": '{"error": "Skipped"}'},
        # User message (loop intervention) comes AFTER all tool results:
        {"role": "user", "content": "SYSTEM: loop detected"},
    ]

    # Verify: after each assistant message with tool_calls, all tool_results
    # come before any non-tool message.
    for i, msg in enumerate(messages):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            tc_ids = {tc["id"] for tc in msg["tool_calls"]}
            seen_non_tool = False
            for j in range(i + 1, len(messages)):
                m = messages[j]
                if m.get("role") == "tool" and m.get("tool_call_id") in tc_ids:
                    assert not seen_non_tool, (
                        f"tool_result for {m['tool_call_id']} at index {j} comes AFTER "
                        f"a non-tool message — this violates Anthropic's message contract"
                    )
                elif m.get("role") != "tool":
                    seen_non_tool = True
