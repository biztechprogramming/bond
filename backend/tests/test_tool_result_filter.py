"""Tests for tool result filter."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

from backend.app.agent.tool_result_filter import (
    FILTER_THRESHOLD,
    SKIP_TOOLS,
    filter_tool_result,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


_COMMON = {
    "user_message": "Fix the CSS layout issue",
    "last_assistant_content": "Reading the CSS file",
    "utility_model": "gemini/gemini-2.5-flash-lite",
    "utility_kwargs": {},
}


def test_skip_small_results():
    small_result = {"status": "ok", "data": "small"}
    result = _run(filter_tool_result(
        tool_name="file_read", tool_args={"path": "/test.txt"},
        raw_result=small_result, **_COMMON,
    ))
    assert result == json.dumps(small_result)


def test_skip_exempt_tools():
    large_result = {"status": "written", "path": "/test.txt", "padding": "x" * 5000}
    for tool in ("respond", "file_write", "memory_save"):
        result = _run(filter_tool_result(
            tool_name=tool, tool_args={},
            raw_result=large_result, **_COMMON,
        ))
        assert result == json.dumps(large_result), f"{tool} should be skipped"


def test_large_result_calls_utility():
    large_result = {"content": "x" * 5000, "path": "/big.css"}

    mock_response = AsyncMock()
    mock_response.choices = [AsyncMock()]
    mock_response.choices[0].message.content = '{"content": "relevant part", "path": "/big.css"}'

    with patch("backend.app.agent.tool_result_filter.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_response)
        result = _run(filter_tool_result(
            tool_name="file_read", tool_args={"path": "/big.css"},
            raw_result=large_result, **_COMMON,
        ))

    mock_litellm.acompletion.assert_called_once()
    parsed = json.loads(result)
    assert parsed["content"] == "relevant part"


def test_utility_failure_returns_raw():
    large_result = {"content": "x" * 5000}

    with patch("backend.app.agent.tool_result_filter.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(side_effect=Exception("API error"))
        result = _run(filter_tool_result(
            tool_name="file_read", tool_args={"path": "/test.txt"},
            raw_result=large_result, **_COMMON,
        ))

    assert result == json.dumps(large_result)


def test_non_json_utility_response_wrapped():
    large_result = {"content": "x" * 5000}

    mock_response = AsyncMock()
    mock_response.choices = [AsyncMock()]
    mock_response.choices[0].message.content = "Just the relevant CSS classes: .defects-layout, .inspection-container"

    with patch("backend.app.agent.tool_result_filter.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_response)
        result = _run(filter_tool_result(
            tool_name="file_read", tool_args={"path": "/big.css"},
            raw_result=large_result, **_COMMON,
        ))

    parsed = json.loads(result)
    assert "filtered_result" in parsed
