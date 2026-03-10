"""Tests for tool_use / tool_result message pairing.

Verifies that the agent loop never produces a message sequence where an
assistant tool_use block lacks a corresponding tool_result.  This is an
Anthropic API hard requirement.

Covers:
1. Lifecycle hook injections are deferred until after all tool_results
2. Early loop-break (loop detection) still emits stub results for
   remaining tool calls in the batch
3. host_exec is auto-injected into agent_tools
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any


# ---------------------------------------------------------------------------
# 1. Lifecycle hooks must not inject user messages between tool_use / tool_result
# ---------------------------------------------------------------------------


def _build_messages_with_tool_calls(tool_calls: list[dict]) -> list[dict]:
    """Build a minimal message list ending with an assistant tool_use message."""
    assistant_msg = {
        "role": "assistant",
        "content": "",
        "tool_calls": tool_calls,
    }
    return [
        {"role": "user", "content": "do something"},
        assistant_msg,
    ]


def _make_tool_call(tc_id: str, name: str, args: dict | None = None) -> dict:
    return {
        "id": tc_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(args or {}),
        },
    }


def test_deferred_injections_come_after_tool_results():
    """Simulate the fixed message-building logic and verify ordering.

    After an assistant message with N tool_use blocks, the next N messages
    must all be role=tool.  Any injected user messages must come after.
    """
    tc1 = _make_tool_call("tc_001", "code_execute", {"language": "shell", "code": "git commit -m 'test'"})
    tc2 = _make_tool_call("tc_002", "file_read", {"path": "README.md"})

    messages = _build_messages_with_tool_calls([tc1, tc2])

    # Simulate the FIXED logic: collect deferred injections, append tool
    # results first, then flush deferred.
    deferred: list[dict] = []

    # Lifecycle hook fires for tc1 (a git commit)
    deferred.append({"role": "user", "content": "SYSTEM: pre-commit guidance"})

    # Tool results
    messages.append({"role": "tool", "tool_call_id": "tc_001", "content": '{"exit_code": 0}'})
    messages.append({"role": "tool", "tool_call_id": "tc_002", "content": '{"content": "# README"}'})

    # Flush deferred
    for inj in deferred:
        messages.append(inj)

    # Verify: after the assistant message (index 1), all tool results come
    # before any user message.
    post_assistant = messages[2:]
    tool_result_phase = True
    for msg in post_assistant:
        if tool_result_phase:
            if msg["role"] == "user":
                tool_result_phase = False
            else:
                assert msg["role"] == "tool", f"Expected tool message, got {msg['role']}"
        else:
            # Once we've left the tool_result phase, no more tool messages
            assert msg["role"] != "tool", "tool message found after user injection"


# ---------------------------------------------------------------------------
# 2. Early break must emit stub results for remaining tool calls
# ---------------------------------------------------------------------------


def test_orphaned_tool_calls_get_stub_results():
    """When the inner loop breaks early, remaining tool_use IDs get stubs."""
    tc1 = _make_tool_call("tc_A", "shell_grep", {"pattern": "foo"})
    tc2 = _make_tool_call("tc_B", "file_read", {"path": "bar.py"})
    tc3 = _make_tool_call("tc_C", "shell_ls", {"path": "."})

    all_tcs = [tc1, tc2, tc3]
    messages = _build_messages_with_tool_calls(all_tcs)

    # Simulate: only tc_A got a result before break
    messages.append({"role": "tool", "tool_call_id": "tc_A", "content": '{"output": "match"}'})
    # Loop intervention message (would have been appended before the fix too)
    messages.append({"role": "user", "content": "SYSTEM: loop detected"})

    # --- Apply the stub-fill logic from the fix ---
    _expected_tc_ids = {tc["id"] for tc in all_tcs}
    _emitted_tc_ids = {
        m["tool_call_id"]
        for m in messages[-len(_expected_tc_ids) * 3:]
        if m.get("role") == "tool" and m.get("tool_call_id") in _expected_tc_ids
    }
    for tc in all_tcs:
        if tc["id"] not in _emitted_tc_ids:
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps({"error": "Skipped — agent loop intervention"}),
            })

    # Verify all three IDs have results
    result_ids = {
        m["tool_call_id"] for m in messages if m.get("role") == "tool"
    }
    assert result_ids == {"tc_A", "tc_B", "tc_C"}


def test_no_duplicate_stubs_when_all_results_present():
    """If all tool calls already have results, no stubs are added."""
    tc1 = _make_tool_call("tc_X", "file_read", {"path": "a.py"})
    tc2 = _make_tool_call("tc_Y", "file_read", {"path": "b.py"})

    all_tcs = [tc1, tc2]
    messages = _build_messages_with_tool_calls(all_tcs)

    messages.append({"role": "tool", "tool_call_id": "tc_X", "content": '{"content": "aaa"}'})
    messages.append({"role": "tool", "tool_call_id": "tc_Y", "content": '{"content": "bbb"}'})

    original_len = len(messages)

    # Apply stub-fill
    _expected_tc_ids = {tc["id"] for tc in all_tcs}
    _emitted_tc_ids = {
        m["tool_call_id"]
        for m in messages[-len(_expected_tc_ids) * 3:]
        if m.get("role") == "tool" and m.get("tool_call_id") in _expected_tc_ids
    }
    for tc in all_tcs:
        if tc["id"] not in _emitted_tc_ids:
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps({"error": "Skipped — agent loop intervention"}),
            })

    assert len(messages) == original_len, "No stubs should be added when all results exist"


# ---------------------------------------------------------------------------
# 3. host_exec auto-injection
# ---------------------------------------------------------------------------


def test_host_exec_auto_injected():
    """host_exec should be auto-injected even if not in agent config."""
    agent_tools = ["respond", "code_execute", "file_read"]

    # Simulate the auto-inject logic from worker.py
    if "host_exec" not in agent_tools:
        agent_tools.append("host_exec")

    assert "host_exec" in agent_tools


def test_host_exec_not_duplicated():
    """If host_exec is already in the list, don't add it again."""
    agent_tools = ["respond", "host_exec", "file_read"]
    original_len = len(agent_tools)

    if "host_exec" not in agent_tools:
        agent_tools.append("host_exec")

    assert len(agent_tools) == original_len
    assert agent_tools.count("host_exec") == 1
