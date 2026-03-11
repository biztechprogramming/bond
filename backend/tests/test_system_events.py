"""Tests for system event enqueuing from the coding agent monitor."""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestCodingAgentSystemEvents:
    """Test that CodingAgentSession._enqueue_system_event works correctly."""

    @pytest.mark.asyncio
    async def test_enqueue_on_success(self):
        """System event is enqueued when coding agent completes successfully."""
        from backend.app.agent.tools.coding_agent import CodingAgentSession

        # Create a minimal session
        mock_process = MagicMock()
        mock_process.elapsed = 42.1
        mock_process.working_directory = "/workspace/test"
        mock_watcher = MagicMock()

        session = CodingAgentSession(
            process=mock_process,
            watcher=mock_watcher,
            conversation_id="conv-001",
            agent_type="claude",
            baseline_commit="abc12345",
            branch="feature/test",
        )
        session.exit_code = 0
        session.final_summary = "Coding agent (claude) completed in 42.1s"

        with patch("backend.app.core.spacetimedb.get_stdb") as mock_get_stdb:
            mock_stdb = AsyncMock()
            mock_stdb.call_reducer = AsyncMock(return_value=True)
            mock_get_stdb.return_value = mock_stdb

            await session._enqueue_system_event("completed", "3 files changed")

            # Verify reducer was called
            mock_stdb.call_reducer.assert_called_once()
            call_args = mock_stdb.call_reducer.call_args[0]

            assert call_args[0] == "enqueue_system_event"
            reducer_args = call_args[1]
            assert reducer_args[1] == "conv-001"  # conversationId
            assert reducer_args[3] == "coding_agent_done"  # eventType
            assert reducer_args[4] == "Coding agent (claude) completed in 42.1s"  # summary

            # Verify metadata JSON
            metadata = json.loads(reducer_args[5])
            assert metadata["agent_type"] == "claude"
            assert metadata["exit_code"] == 0
            assert metadata["git_stat"] == "3 files changed"
            assert metadata["branch"] == "feature/test"

    @pytest.mark.asyncio
    async def test_enqueue_on_failure(self):
        """System event uses coding_agent_failed type on non-zero exit."""
        from backend.app.agent.tools.coding_agent import CodingAgentSession

        mock_process = MagicMock()
        mock_process.elapsed = 10.0
        mock_process.working_directory = "/workspace/test"
        mock_watcher = MagicMock()

        session = CodingAgentSession(
            process=mock_process,
            watcher=mock_watcher,
            conversation_id="conv-002",
            agent_type="codex",
            baseline_commit="def67890",
            branch=None,
        )
        session.exit_code = 1
        session.final_summary = "Coding agent (codex) failed in 10.0s"

        with patch("backend.app.core.spacetimedb.get_stdb") as mock_get_stdb:
            mock_stdb = AsyncMock()
            mock_stdb.call_reducer = AsyncMock(return_value=True)
            mock_get_stdb.return_value = mock_stdb

            await session._enqueue_system_event("failed", "")

            call_args = mock_stdb.call_reducer.call_args[0]
            reducer_args = call_args[1]
            assert reducer_args[3] == "coding_agent_failed"

    @pytest.mark.asyncio
    async def test_enqueue_graceful_on_stdb_error(self):
        """System event enqueue failure is logged but doesn't crash the monitor."""
        from backend.app.agent.tools.coding_agent import CodingAgentSession

        mock_process = MagicMock()
        mock_process.elapsed = 5.0
        mock_process.working_directory = "/workspace/test"
        mock_watcher = MagicMock()

        session = CodingAgentSession(
            process=mock_process,
            watcher=mock_watcher,
            conversation_id="conv-003",
            agent_type="claude",
            baseline_commit="ghi11111",
            branch=None,
        )
        session.exit_code = 0
        session.final_summary = "Done"

        with patch("backend.app.core.spacetimedb.get_stdb") as mock_get_stdb:
            mock_get_stdb.side_effect = Exception("SpacetimeDB unreachable")

            # Should not raise
            await session._enqueue_system_event("completed", "")

    @pytest.mark.asyncio
    async def test_enqueue_graceful_on_reducer_failure(self):
        """Reducer returning False is logged but doesn't crash."""
        from backend.app.agent.tools.coding_agent import CodingAgentSession

        mock_process = MagicMock()
        mock_process.elapsed = 5.0
        mock_process.working_directory = "/workspace/test"
        mock_watcher = MagicMock()

        session = CodingAgentSession(
            process=mock_process,
            watcher=mock_watcher,
            conversation_id="conv-004",
            agent_type="claude",
            baseline_commit="jkl22222",
            branch=None,
        )
        session.exit_code = 0
        session.final_summary = "Done"

        with patch("backend.app.core.spacetimedb.get_stdb") as mock_get_stdb:
            mock_stdb = AsyncMock()
            mock_stdb.call_reducer = AsyncMock(return_value=False)
            mock_get_stdb.return_value = mock_stdb

            # Should not raise
            await session._enqueue_system_event("completed", "")
