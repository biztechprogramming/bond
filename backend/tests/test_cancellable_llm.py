"""Tests for cancellable LLM call — Design doc 037 §5.2.1 / §9.

Tests interrupt during LLM call, normal completion, and pending messages.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.worker import _cancellable_llm_call


class TestCancellableLlmCall:
    @pytest.mark.asyncio
    async def test_normal_completion(self) -> None:
        """LLM call completes before interrupt → returns response."""
        interrupt_event = asyncio.Event()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]

        with patch("backend.app.worker.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(return_value=mock_response)

            result = await _cancellable_llm_call(
                interrupt_event,
                model="test-model",
                messages=[{"role": "user", "content": "hello"}],
            )

        assert result is not None
        assert result.choices

    @pytest.mark.asyncio
    async def test_interrupt_during_call(self) -> None:
        """Interrupt fires during LLM call → returns None."""
        interrupt_event = asyncio.Event()

        async def slow_completion(**kwargs):
            await asyncio.sleep(10)  # simulate slow LLM
            return MagicMock()

        with patch("backend.app.worker.litellm") as mock_litellm:
            mock_litellm.acompletion = slow_completion

            # Fire interrupt after a short delay
            async def fire_interrupt():
                await asyncio.sleep(0.1)
                interrupt_event.set()

            asyncio.create_task(fire_interrupt())

            result = await _cancellable_llm_call(
                interrupt_event,
                model="test-model",
                messages=[{"role": "user", "content": "hello"}],
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_interrupt_already_set(self) -> None:
        """If interrupt is already set, returns None immediately."""
        interrupt_event = asyncio.Event()
        interrupt_event.set()

        async def slow_completion(**kwargs):
            await asyncio.sleep(10)
            return MagicMock()

        with patch("backend.app.worker.litellm") as mock_litellm:
            mock_litellm.acompletion = slow_completion

            result = await _cancellable_llm_call(
                interrupt_event,
                model="test-model",
                messages=[{"role": "user", "content": "hello"}],
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_llm_exception_propagates(self) -> None:
        """If LLM call raises, the exception propagates."""
        interrupt_event = asyncio.Event()

        async def failing_completion(**kwargs):
            raise RuntimeError("API error")

        with patch("backend.app.worker.litellm") as mock_litellm:
            mock_litellm.acompletion = failing_completion

            with pytest.raises(RuntimeError, match="API error"):
                await _cancellable_llm_call(
                    interrupt_event,
                    model="test-model",
                    messages=[{"role": "user", "content": "hello"}],
                )
