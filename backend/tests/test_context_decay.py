"""Tests for progressive decay on tool results."""

import json
import pytest
from backend.app.agent.context_decay import apply_progressive_decay, _estimate_tokens


def _make_messages(turns: list[list[dict]]) -> list[dict]:
    """Build a flat message list from turns. Each turn is [user, assistant, tool?, ...]."""
    result = []
    for turn in turns:
        result.extend(turn)
    return result


def _tool_msg(content: str, tool_call_id: str = "tc1") -> dict:
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


def _user_msg(content: str = "do something") -> dict:
    return {"role": "user", "content": content}


def _assistant_msg(content: str = "ok", tool_calls: list | None = None) -> dict:
    msg: dict = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


class TestProgressiveDecay:
    def test_empty_messages(self):
        assert apply_progressive_decay([]) == []

    def test_small_tool_results_unchanged(self):
        """Tool results under 200 tokens should pass through."""
        msgs = [
            _user_msg(),
            _assistant_msg("", [{"id": "tc1", "function": {"name": "file_read", "arguments": "{}"}}]),
            _tool_msg("small result"),
        ]
        result = apply_progressive_decay(msgs)
        assert result[2]["content"] == "small result"

    def test_turn_0_capped(self):
        """Fresh tool result should be capped at MAX_TOOL_RESULT_TOKENS."""
        big_content = "x" * 10000  # ~2500 tokens
        msgs = [
            _user_msg(),
            _assistant_msg("", [{"id": "tc1", "function": {"name": "test", "arguments": "{}"}}]),
            _tool_msg(big_content),
        ]
        result = apply_progressive_decay(msgs)
        # Should be smaller than original
        assert len(result[2]["content"]) < len(big_content)

    def test_old_tool_results_summarized(self):
        """Tool results from many turns ago should be heavily compressed."""
        big_file = json.dumps({"file_path": "src/main.py", "content": "line\n" * 200})
        msgs = []
        # Add the tool result 8 turns ago
        msgs.append(_user_msg("read the file"))
        msgs.append(_assistant_msg("", [{"id": "tc1", "function": {"name": "file_read", "arguments": '{"path":"src/main.py"}'}}]))
        msgs.append(_tool_msg(big_file, "tc1"))
        # Add 8 more turns
        for i in range(8):
            msgs.append(_user_msg(f"turn {i}"))
            msgs.append(_assistant_msg(f"response {i}"))

        result = apply_progressive_decay(msgs)
        # The old tool result should be heavily compressed
        old_tool = result[2]
        assert len(old_tool["content"]) < 200

    def test_code_execute_keeps_tail(self):
        """Recent code execution should keep last N lines of output."""
        output = "\n".join(f"line {i}" for i in range(100))
        exec_result = json.dumps({"exit_code": 0, "stdout": output})
        msgs = [
            _user_msg("run it"),
            _assistant_msg("", [{"id": "tc1", "function": {"name": "code_execute", "arguments": "{}"}}]),
            _tool_msg(exec_result, "tc1"),
            _user_msg("what happened?"),  # 1 turn later
        ]
        result = apply_progressive_decay(msgs)
        # Should still have some content (tail lines)
        assert "line 99" in result[2]["content"]

    def test_non_tool_messages_unchanged(self):
        msgs = [
            _user_msg("hello"),
            _assistant_msg("hi there"),
        ]
        result = apply_progressive_decay(msgs)
        assert result == msgs

    def test_file_read_head_tail(self):
        """Recent file reads should keep head + tail lines."""
        lines = [f"line {i}: some content here" for i in range(100)]
        file_result = json.dumps({"file_path": "test.py", "content": "\n".join(lines)})
        msgs = [
            _user_msg("read test.py"),
            _assistant_msg("", [{"id": "tc1", "function": {"name": "file_read", "arguments": "{}"}}]),
            _tool_msg(file_result, "tc1"),
            _user_msg("now what?"),  # 1 turn later
        ]
        result = apply_progressive_decay(msgs)
        content = result[2]["content"]
        # Should have head lines
        assert "line 0" in content
        # Should have tail lines
        assert "line 99" in content
        # Should have omission marker
        assert "omitted" in content

    def test_search_memory_trimmed(self):
        """Recent memory search should keep only top 2 results."""
        results = [{"content": f"memory {i} " + "detail " * 50, "score": 0.9 - i * 0.1} for i in range(5)]
        search_result = json.dumps({"results": results})
        msgs = [
            _user_msg("what do you remember?"),
            _assistant_msg("", [{"id": "tc1", "function": {"name": "search_memory", "arguments": "{}"}}]),
            _tool_msg(search_result, "tc1"),
            _user_msg("ok"),  # 1 turn later
        ]
        result = apply_progressive_decay(msgs)
        parsed = json.loads(result[2]["content"])
        assert len(parsed["results"]) == 2
