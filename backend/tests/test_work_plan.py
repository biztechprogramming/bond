"""Tests for work_plan tool — all operations go through the Gateway API (SpacetimeDB).

SQLite is gone. These tests mock httpx calls to the Gateway.
"""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.agent.tools.work_plan import (
    handle_work_plan,
    load_active_plan,
    format_recovery_context,
    checkpoint_active_plan,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GATEWAY = "http://localhost:18789"
AGENT_ID = "test-agent"
CONV_ID = "conv-1"


def _ctx():
    return {"agent_id": AGENT_ID, "conversation_id": CONV_ID}


def _run(coro):
    return asyncio.run(coro)


def _mock_response(json_data, status_code=200):
    """Build a mock httpx Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        from httpx import HTTPStatusError, Request, Response
        resp.raise_for_status.side_effect = HTTPStatusError(
            "error", request=MagicMock(), response=MagicMock()
        )
    return resp


def _api_env():
    """Patch BOND_API_URL at the module level (it's captured at import time)."""
    return patch("backend.app.agent.tools.work_plan._BOND_API_URL", GATEWAY)


def _patch_client(responses: list):
    """
    Patch httpx.AsyncClient so that get/post/put calls return responses in order.
    ``responses`` is a list of (method, return_value) pairs or just return_values
    (which match any method).
    """
    client_mock = AsyncMock()
    call_queue = list(responses)

    def _next_resp(*args, **kwargs):
        return call_queue.pop(0)

    client_mock.get = AsyncMock(side_effect=_next_resp)
    client_mock.post = AsyncMock(side_effect=_next_resp)
    client_mock.put = AsyncMock(side_effect=_next_resp)

    ctx_mgr = AsyncMock()
    ctx_mgr.__aenter__ = AsyncMock(return_value=client_mock)
    ctx_mgr.__aexit__ = AsyncMock(return_value=False)

    return patch("httpx.AsyncClient", return_value=ctx_mgr), client_mock


# ---------------------------------------------------------------------------
# handle_work_plan — no BOND_API_URL
# ---------------------------------------------------------------------------

class TestNoApiUrl:
    def test_returns_error_when_api_url_missing(self):
        with patch("backend.app.agent.tools.work_plan._BOND_API_URL", ""):
            result = _run(handle_work_plan({"action": "create_plan", "title": "X"}, _ctx()))
        assert "error" in result
        assert "BOND_API_URL" in result["error"]

    def test_missing_action(self):
        with _api_env():
            result = _run(handle_work_plan({}, _ctx()))
        assert "error" in result


# ---------------------------------------------------------------------------
# create_plan
# ---------------------------------------------------------------------------

class TestCreatePlan:
    def test_create_plan_basic(self):
        api_resp = {"plan_id": "PLAN1", "id": "PLAN1", "title": "Test Plan", "status": "active"}

        patcher, client = _patch_client([_mock_response(api_resp, 201)])
        with _api_env(), patcher:
            result = _run(handle_work_plan({"action": "create_plan", "title": "Test Plan"}, _ctx()))

        assert result["plan_id"] == "PLAN1"
        assert result["status"] == "active"
        # Confirm POST was called
        client.post.assert_called_once()
        call_url = client.post.call_args[0][0]
        assert "/api/v1/plans" in call_url

    def test_create_plan_missing_title(self):
        with _api_env():
            result = _run(handle_work_plan({"action": "create_plan"}, _ctx()))
        assert "error" in result

    def test_create_plan_api_error(self):
        patcher, _ = _patch_client([_mock_response({}, 500)])
        with _api_env(), patcher:
            result = _run(handle_work_plan({"action": "create_plan", "title": "Test"}, _ctx()))
        assert "error" in result


# ---------------------------------------------------------------------------
# add_item
# ---------------------------------------------------------------------------

class TestAddItem:
    def test_add_item_basic(self):
        api_resp = {
            "item_id": "ITEM1", "id": "ITEM1",
            "plan_id": "PLAN1", "title": "Step 1",
            "status": "new", "ordinal": 0,
        }
        patcher, client = _patch_client([_mock_response(api_resp, 201)])
        with _api_env(), patcher:
            result = _run(handle_work_plan(
                {"action": "add_item", "plan_id": "PLAN1", "title": "Step 1"}, _ctx()
            ))
        assert result["item_id"] == "ITEM1"
        assert result["ordinal"] == 0

    def test_add_item_missing_plan_id(self):
        with _api_env():
            result = _run(handle_work_plan({"action": "add_item", "title": "Step"}, _ctx()))
        assert "error" in result

    def test_add_item_missing_title(self):
        with _api_env():
            result = _run(handle_work_plan({"action": "add_item", "plan_id": "P1"}, _ctx()))
        assert "error" in result


# ---------------------------------------------------------------------------
# update_item
# ---------------------------------------------------------------------------

class TestUpdateItem:
    def test_update_status(self):
        # GET returns current status (different from requested) → idempotency check passes
        get_resp = {"item_id": "ITEM1", "status": "new"}
        api_resp = {"item_id": "ITEM1", "updated": True}
        patcher, client = _patch_client([_mock_response(get_resp), _mock_response(api_resp)])
        with _api_env(), patcher:
            result = _run(handle_work_plan(
                {"action": "update_item", "plan_id": "PLAN1", "item_id": "ITEM1", "status": "in_progress"}, _ctx()
            ))
        assert result.get("item_id") == "ITEM1"
        client.put.assert_called_once()

    def test_update_item_missing_item_id(self):
        with _api_env():
            result = _run(handle_work_plan({"action": "update_item", "status": "done"}, _ctx()))
        assert "error" in result

    def test_update_item_api_error(self):
        # GET for idempotency check returns current state, PUT returns 404 error
        get_resp = _mock_response({"status": "in_progress"})
        patcher, _ = _patch_client([get_resp, _mock_response({}, 404)])
        with _api_env(), patcher:
            result = _run(handle_work_plan(
                {"action": "update_item", "item_id": "ITEM1", "status": "done"}, _ctx()
            ))
        assert "error" in result


# ---------------------------------------------------------------------------
# complete_plan
# ---------------------------------------------------------------------------

class TestCompletePlan:
    def test_complete_plan(self):
        api_resp = {"plan_id": "PLAN1", "status": "completed"}
        patcher, client = _patch_client([_mock_response(api_resp)])
        with _api_env(), patcher:
            result = _run(handle_work_plan(
                {"action": "complete_plan", "plan_id": "PLAN1", "status": "completed"}, _ctx()
            ))
        assert result["plan_id"] == "PLAN1"
        assert result["status"] == "completed"
        client.post.assert_called_once()

    def test_complete_plan_missing_plan_id(self):
        with _api_env():
            result = _run(handle_work_plan({"action": "complete_plan"}, _ctx()))
        assert "error" in result


# ---------------------------------------------------------------------------
# get_plan
# ---------------------------------------------------------------------------

class TestGetPlan:
    def test_get_plan(self):
        plan = {
            "id": "PLAN1", "title": "My Plan", "status": "active",
            "agent_id": AGENT_ID, "items": [
                {"id": "I1", "title": "Step 1", "status": "new", "ordinal": 0},
                {"id": "I2", "title": "Step 2", "status": "new", "ordinal": 1},
            ],
        }
        patcher, client = _patch_client([_mock_response(plan)])
        with _api_env(), patcher:
            result = _run(handle_work_plan({"action": "get_plan", "plan_id": "PLAN1"}, _ctx()))
        assert result["title"] == "My Plan"
        assert len(result["items"]) == 2
        client.get.assert_called_once()

    def test_get_plan_missing_plan_id(self):
        with _api_env():
            result = _run(handle_work_plan({"action": "get_plan"}, _ctx()))
        assert "error" in result

    def test_get_plan_not_found(self):
        patcher, _ = _patch_client([_mock_response({"error": "not found"}, 404)])
        with _api_env(), patcher:
            result = _run(handle_work_plan({"action": "get_plan", "plan_id": "NONE"}, _ctx()))
        assert "error" in result


# ---------------------------------------------------------------------------
# unknown action
# ---------------------------------------------------------------------------

class TestUnknownAction:
    def test_unknown_action(self):
        # No HTTP calls needed — rejected before the request
        patcher, client = _patch_client([])
        with _api_env(), patcher:
            result = _run(handle_work_plan({"action": "fly_to_moon"}, _ctx()))
        assert "error" in result
        client.post.assert_not_called()
        client.get.assert_not_called()
        client.put.assert_not_called()


# ---------------------------------------------------------------------------
# load_active_plan
# ---------------------------------------------------------------------------

class TestLoadActivePlan:
    def test_returns_none_when_no_api_url(self):
        with patch("backend.app.agent.tools.work_plan._BOND_API_URL", ""):
            result = _run(load_active_plan(None, AGENT_ID))
        assert result is None

    def test_returns_none_when_no_plans(self):
        patcher, _ = _patch_client([_mock_response([])])
        with _api_env(), patcher:
            result = _run(load_active_plan(None, AGENT_ID))
        assert result is None

    def test_returns_plan_with_items(self):
        list_resp = [{"id": "PLAN1", "status": "active", "title": "Active Plan"}]
        plan_resp = {
            "id": "PLAN1", "title": "Active Plan", "status": "active",
            "agent_id": AGENT_ID,
            "items": [{"id": "I1", "title": "Step 1", "status": "new", "ordinal": 0}],
        }
        patcher, _ = _patch_client([_mock_response(list_resp), _mock_response(plan_resp)])
        with _api_env(), patcher:
            result = _run(load_active_plan(None, AGENT_ID))
        assert result is not None
        assert result["title"] == "Active Plan"
        assert len(result["items"]) == 1

    def test_returns_none_on_api_error(self):
        patcher, _ = _patch_client([_mock_response({}, 500)])
        with _api_env(), patcher:
            result = _run(load_active_plan(None, AGENT_ID))
        assert result is None


# ---------------------------------------------------------------------------
# checkpoint_active_plan
# ---------------------------------------------------------------------------

class TestCheckpointActivePlan:
    def test_returns_false_when_no_api_url(self):
        with patch("backend.app.agent.tools.work_plan._BOND_API_URL", ""):
            result = _run(checkpoint_active_plan(None, AGENT_ID))
        assert result is False

    def test_returns_false_when_no_active_plan(self):
        patcher, _ = _patch_client([_mock_response([])])
        with _api_env(), patcher:
            result = _run(checkpoint_active_plan(None, AGENT_ID))
        assert result is False

    def test_returns_false_when_no_in_progress_item(self):
        list_resp = [{"id": "PLAN1", "status": "active"}]
        plan_resp = {
            "id": "PLAN1", "agent_id": AGENT_ID, "status": "active",
            "items": [{"id": "I1", "title": "Step 1", "status": "new", "ordinal": 0, "notes": []}],
        }
        patcher, _ = _patch_client([_mock_response(list_resp), _mock_response(plan_resp)])
        with _api_env(), patcher:
            result = _run(checkpoint_active_plan(None, AGENT_ID))
        assert result is False

    def test_checkpoints_in_progress_item(self):
        list_resp = [{"id": "PLAN1", "status": "active"}]
        plan_resp = {
            "id": "PLAN1", "agent_id": AGENT_ID, "status": "active",
            "items": [
                {"id": "I1", "title": "Step 1", "status": "in_progress", "ordinal": 0, "notes": []},
            ],
        }
        put_resp = {"item_id": "I1", "updated": True}

        patcher, client = _patch_client([
            _mock_response(list_resp),   # GET /plans?...
            _mock_response(plan_resp),   # GET /plans/PLAN1
            _mock_response(put_resp),    # PUT /plans/PLAN1/items/I1
        ])
        with _api_env(), patcher:
            result = _run(checkpoint_active_plan(None, AGENT_ID, "Max iterations"))

        assert result is True
        client.put.assert_called_once()
        put_body = client.put.call_args[1]["json"]
        notes = put_body["notes"]
        assert any("Max iterations" in n["text"] for n in notes)


# ---------------------------------------------------------------------------
# format_recovery_context
# ---------------------------------------------------------------------------

class TestFormatRecoveryContext:
    def test_format_basic(self):
        plan = {
            "title": "Recovery Test",
            "id": "PLAN1",
            "status": "active",
            "items": [
                {"id": "I1", "title": "Done step", "status": "done",
                 "ordinal": 0, "notes": [{"at": "2024-01-01", "text": "All good"}], "files_changed": []},
                {"id": "I2", "title": "Current step", "status": "in_progress",
                 "ordinal": 1, "notes": [{"at": "2024-01-01", "text": "Working"}], "files_changed": []},
                {"id": "I3", "title": "Future step", "status": "new",
                 "ordinal": 2, "notes": [], "files_changed": []},
            ],
        }
        text = format_recovery_context(plan)
        assert "Recovery Test" in text
        assert "Done step" in text
        assert "Current step" in text
        assert "Future step" in text
        assert "\u2705" in text   # ✅ done
        assert "\U0001f504" in text  # 🔄 in_progress
