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
    result_json, cost = _run(filter_tool_result(
        tool_name="file_read", tool_args={"path": "/test.txt"},
        raw_result=small_result, **_COMMON,
    ))
    assert result_json == json.dumps(small_result)
    assert cost == 0.0


def test_skip_exempt_tools():
    large_result = {"status": "written", "path": "/test.txt", "padding": "x" * 5000}
    for tool in ("respond", "file_write", "memory_save"):
        result_json, cost = _run(filter_tool_result(
            tool_name=tool, tool_args={},
            raw_result=large_result, **_COMMON,
        ))
        assert result_json == json.dumps(large_result), f"{tool} should be skipped"
        assert cost == 0.0


def test_large_result_calls_utility():
    # Must exceed FILTER_THRESHOLD (6000 chars) and not be caught by rule_based_prune.
    # Use a non-file_read tool with large output so rule_based_prune returns None.
    large_result = {"output": "x" * 8000, "url": "https://example.com"}

    mock_response = AsyncMock()
    mock_response.choices = [AsyncMock()]
    mock_response.choices[0].message.content = '{"output": "relevant part", "url": "https://example.com"}'

    with patch("backend.app.agent.tool_result_filter.litellm") as mock_litellm, \
         patch("backend.app.agent.tool_result_filter._litellm_completion_cost", return_value=0.001):
        mock_litellm.acompletion = AsyncMock(return_value=mock_response)
        result_json, cost = _run(filter_tool_result(
            tool_name="web_read", tool_args={"url": "https://example.com"},
            raw_result=large_result, **_COMMON,
        ))

    mock_litellm.acompletion.assert_called_once()
    parsed = json.loads(result_json)
    assert parsed["output"] == "relevant part"
    assert cost == 0.001


def test_utility_failure_returns_raw():
    large_result = {"output": "x" * 8000}

    with patch("backend.app.agent.tool_result_filter.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(side_effect=Exception("API error"))
        result_json, cost = _run(filter_tool_result(
            tool_name="web_read", tool_args={"url": "https://example.com"},
            raw_result=large_result, **_COMMON,
        ))

    assert result_json == json.dumps(large_result)
    assert cost == 0.0


def test_non_json_utility_response_wrapped():
    large_result = {"output": "x" * 8000}

    mock_response = AsyncMock()
    mock_response.choices = [AsyncMock()]
    mock_response.choices[0].message.content = "Just the relevant CSS classes: .defects-layout, .inspection-container"

    with patch("backend.app.agent.tool_result_filter.litellm") as mock_litellm, \
         patch("backend.app.agent.tool_result_filter._litellm_completion_cost", return_value=0.0005):
        mock_litellm.acompletion = AsyncMock(return_value=mock_response)
        result_json, cost = _run(filter_tool_result(
            tool_name="web_read", tool_args={"url": "https://example.com"},
            raw_result=large_result, **_COMMON,
        ))

    parsed = json.loads(result_json)
    assert "filtered_result" in parsed
    assert cost == 0.0005
