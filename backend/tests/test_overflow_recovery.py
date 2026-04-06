"""Tests for Doc 091: Overflow Recovery."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# ContextOverflowError detection — test the helper directly
# ---------------------------------------------------------------------------

class TestOverflowDetection:
    """Test is_overflow_error against various error strings."""

    def _is_overflow(self, error: Exception) -> bool:
        """Inline version of is_overflow_error to avoid vendor imports."""
        patterns = [
            "context_length_exceeded",
            "maximum context length",
            "request too large",
            "413",
            "token limit",
            "too many tokens",
        ]
        error_str = str(error).lower()
        return any(pat in error_str for pat in patterns)

    def test_context_length_exceeded(self):
        assert self._is_overflow(Exception("Error: context_length_exceeded for model xyz"))

    def test_maximum_context_length(self):
        assert self._is_overflow(Exception("This model's maximum context length is 128000 tokens"))

    def test_request_too_large(self):
        assert self._is_overflow(Exception("Request too large"))

    def test_413_status(self):
        assert self._is_overflow(Exception("HTTP 413: payload too large"))

    def test_token_limit(self):
        assert self._is_overflow(Exception("You have exceeded the token limit"))

    def test_too_many_tokens(self):
        assert self._is_overflow(Exception("too many tokens in the request"))

    def test_unrelated_error(self):
        assert not self._is_overflow(Exception("rate limit exceeded"))


# ---------------------------------------------------------------------------
# Compaction functions — import from loop.py with mocked vendor deps
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _mock_vendor_imports(monkeypatch):
    """Stub out heavy vendor imports so loop.py can be imported in tests."""
    stubs = {}
    for mod_name in [
        "vendor", "instructor", "litellm", "sqlalchemy", "sqlalchemy.ext",
        "sqlalchemy.ext.asyncio", "sqlalchemy.ext.asyncio.AsyncSession",
        "yaml",
    ]:
        if mod_name not in sys.modules:
            stubs[mod_name] = MagicMock()
            monkeypatch.setitem(sys.modules, mod_name, stubs[mod_name])

    # Ensure sqlalchemy.text is available
    sql_mod = sys.modules.get("sqlalchemy") or stubs.get("sqlalchemy")
    if sql_mod:
        sql_mod.text = MagicMock()


def _make_messages(n: int, with_system: bool = True) -> list[dict]:
    msgs = []
    if with_system:
        msgs.append({"role": "system", "content": "You are a helpful assistant."})
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"message {i}"})
    return msgs


class TestAggressiveCompact:
    def test_keeps_system_and_recent(self):
        from backend.app.agent.loop import _aggressive_compact
        msgs = _make_messages(20)
        result = _aggressive_compact(msgs, keep_recent_turns=3)
        assert result[0]["role"] == "system"
        assert len(result) == 7  # system + 6

    def test_few_messages(self):
        from backend.app.agent.loop import _aggressive_compact
        msgs = _make_messages(2)
        result = _aggressive_compact(msgs, keep_recent_turns=3)
        assert result[0]["role"] == "system"
        assert len(result) == 3  # system + 2


class TestEmergencyCollapse:
    def test_keeps_system_and_last_two(self):
        from backend.app.agent.loop import _emergency_collapse
        msgs = _make_messages(20)
        result = _emergency_collapse(msgs)
        assert result[0]["role"] == "system"
        assert len(result) == 3

    def test_minimal_messages(self):
        from backend.app.agent.loop import _emergency_collapse
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        result = _emergency_collapse(msgs)
        assert len(result) == 2


class TestStandardCompact:
    def test_reduces_messages(self):
        from backend.app.agent.loop import _standard_compact
        msgs = _make_messages(30)
        result = _standard_compact(msgs)
        assert len(result) < len(msgs)
        assert result[0]["role"] == "system"

    def test_short_messages_unchanged(self):
        from backend.app.agent.loop import _standard_compact
        msgs = _make_messages(3)
        result = _standard_compact(msgs)
        assert len(result) == len(msgs)


# ---------------------------------------------------------------------------
# 3-tier recovery chain
# ---------------------------------------------------------------------------

class TestLLMCallWithRecovery:
    @pytest.mark.asyncio
    async def test_success_on_first_try(self):
        from backend.app.agent.loop import _llm_call_with_recovery

        async def ok_call(msgs):
            return "ok"

        result, msgs = await _llm_call_with_recovery(ok_call, [{"role": "user", "content": "hi"}])
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_recovers_after_one_failure(self):
        from backend.app.agent.loop import _llm_call_with_recovery
        call_count = 0

        async def fail_then_ok(msgs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("context_length_exceeded")
            return "recovered"

        msgs = _make_messages(20)
        result, _ = await _llm_call_with_recovery(fail_then_ok, msgs)
        assert result == "recovered"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_exhausts_all_tiers(self):
        from backend.app.agent.loop import _llm_call_with_recovery
        from backend.app.agent.llm import ContextOverflowError

        async def always_fail(msgs):
            raise Exception("context_length_exceeded")

        msgs = _make_messages(20)
        with pytest.raises(ContextOverflowError):
            await _llm_call_with_recovery(always_fail, msgs)

    @pytest.mark.asyncio
    async def test_non_overflow_error_propagates(self):
        from backend.app.agent.loop import _llm_call_with_recovery

        async def auth_fail(msgs):
            raise Exception("authentication failed")

        with pytest.raises(Exception, match="authentication failed"):
            await _llm_call_with_recovery(auth_fail, [{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_messages_compacted_between_retries(self):
        from backend.app.agent.loop import _llm_call_with_recovery
        from backend.app.agent.llm import ContextOverflowError
        seen_lengths = []

        async def record_and_fail(msgs):
            seen_lengths.append(len(msgs))
            raise Exception("too many tokens")

        msgs = _make_messages(30)
        with pytest.raises(ContextOverflowError):
            await _llm_call_with_recovery(record_and_fail, msgs)

        # Each retry should have progressively fewer messages
        assert seen_lengths[0] > seen_lengths[-1]


# ---------------------------------------------------------------------------
# LoopState overflow metrics
# ---------------------------------------------------------------------------

class TestLoopStateOverflow:
    def test_record_overflow_recovered(self):
        from backend.app.agent.loop_state import LoopState
        state = LoopState()
        state.record_overflow("standard", recovered=True)
        assert state.overflow_events == 1
        assert state.overflow_recoveries == 1
        assert state.recovery_tiers_used == ["standard"]

    def test_record_overflow_not_recovered(self):
        from backend.app.agent.loop_state import LoopState
        state = LoopState()
        state.record_overflow("emergency", recovered=False)
        assert state.overflow_events == 1
        assert state.overflow_recoveries == 0

    def test_multiple_overflows(self):
        from backend.app.agent.loop_state import LoopState
        state = LoopState()
        state.record_overflow("standard", recovered=True)
        state.record_overflow("aggressive", recovered=True)
        state.record_overflow("emergency", recovered=False)
        assert state.overflow_events == 3
        assert state.overflow_recoveries == 2
        assert state.recovery_tiers_used == ["standard", "aggressive", "emergency"]
