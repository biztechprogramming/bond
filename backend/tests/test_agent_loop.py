"""Tests for the upgraded agent loop with tool-use."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"


async def _apply_sql(db: aiosqlite.Connection, sql_file: Path) -> None:
    sql = sql_file.read_text()
    await db.executescript(sql)


@pytest.fixture()
async def db_session(_clear_settings_cache):
    """Provide a real async DB session with agents tables."""
    import backend.app.db.session as sess

    sess._engine = None
    sess._session_factory = None

    tmpdir = tempfile.mkdtemp(prefix="bond_loop_test_")
    db_path = Path(tmpdir) / "test.db"
    os.environ["BOND_DATABASE_PATH"] = str(db_path)

    async with aiosqlite.connect(db_path) as db:
        from tests.conftest import apply_all_migrations
        await apply_all_migrations(db)

    from backend.app.config import get_settings
    get_settings.cache_clear()

    sess._engine = None
    sess._session_factory = None

    factory = sess.get_session_factory()
    async with factory() as session:
        yield session

    sess._engine = None
    sess._session_factory = None


def _make_llm_response(content=None, tool_calls=None):
    """Build a mock LLM response object."""
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
    """Build a mock tool call object."""
    tc = MagicMock()
    tc.id = call_id
    tc.function = MagicMock()
    tc.function.name = name
    tc.function.arguments = json.dumps(arguments)
    return tc


@pytest.mark.asyncio
async def test_simple_text_response(db_session):
    """LLM returns text directly — no tool loop."""
    text_response = _make_llm_response(content="Hello from Bond!")

    with patch("backend.app.agent.loop.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=text_response)

        from backend.app.agent.loop import agent_turn
        result = await agent_turn("Hello", db=db_session)
        assert result == "Hello from Bond!"


@pytest.mark.asyncio
async def test_tool_call_then_text(db_session):
    """LLM calls a tool, then responds with text."""
    # First call: LLM returns a tool call (respond)
    tool_call = _make_tool_call("call_1", "respond", {"message": "Tool response!"})
    tool_response = _make_llm_response(tool_calls=[tool_call])

    with patch("backend.app.agent.loop.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=tool_response)

        from backend.app.agent.loop import agent_turn
        result = await agent_turn("Test", db=db_session)
        assert result == "Tool response!"


@pytest.mark.asyncio
async def test_multi_step_tool_loop(db_session):
    """LLM calls search_memory, then responds."""
    # First call: tool call to search_memory
    search_call = _make_tool_call("call_1", "search_memory", {"query": "test"})
    first_response = _make_llm_response(tool_calls=[search_call])

    # Second call: text response
    final_response = _make_llm_response(content="Found it!")

    call_count = 0

    async def mock_acompletion(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return first_response
        return final_response

    with patch("backend.app.agent.loop.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(side_effect=mock_acompletion)

        from backend.app.agent.loop import agent_turn
        result = await agent_turn("Search for test", db=db_session)
        assert result == "Found it!"
        assert call_count == 2


@pytest.mark.asyncio
async def test_max_iterations_safety(db_session):
    """Should stop after max_iterations and return safety message."""
    # Always return a tool call to trigger infinite loop
    tool_call = _make_tool_call("call_1", "web_search", {"query": "test"})
    tool_response = _make_llm_response(tool_calls=[tool_call])

    with patch("backend.app.agent.loop.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=tool_response)

        from backend.app.agent.loop import agent_turn
        result = await agent_turn("Loop forever", db=db_session)
        assert "maximum" in result.lower()
        # Default agent has max_iterations=25, so LLM should be called 25 times
        assert mock_litellm.acompletion.call_count == 25


@pytest.mark.asyncio
async def test_fallback_without_db():
    """Without db, should fall back to simple chat_completion."""
    with patch("backend.app.agent.loop.chat_completion", new_callable=AsyncMock) as mock_cc:
        mock_cc.return_value = "Simple response"

        from backend.app.agent.loop import agent_turn
        result = await agent_turn("Hello", db=None)
        assert result == "Simple response"
        mock_cc.assert_called_once()
