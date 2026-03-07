#!/usr/bin/env python3
"""SpacetimeDB Connectivity Test Suite.

Tests that verify the full token chain works end-to-end:
  Container env → Gateway config → SpacetimeDB auth → query/reducer

These tests are designed to FAIL when SPACETIMEDB_TOKEN is not configured,
and PASS once the fix described in docs/fix-spacetimedb-token.md is applied.

Run:  python -m pytest tests/test_spacetimedb_connectivity.py -v
  or: python tests/test_spacetimedb_connectivity.py
"""

import asyncio
import os
import unittest
from unittest.mock import patch

import httpx


# ---------------------------------------------------------------------------
# Config — mirrors bond/gateway/src/config/index.ts defaults
# ---------------------------------------------------------------------------

GATEWAY_URL = os.environ.get("BOND_GATEWAY_URL", "http://host.docker.internal:18792")
SPACETIMEDB_URL = os.environ.get("BOND_SPACETIMEDB_URL", "http://localhost:18787")
SPACETIMEDB_MODULE = os.environ.get("BOND_SPACETIMEDB_MODULE", "bond-core-v2")


def _get_token() -> str:
    """Resolve SpacetimeDB token the same way the gateway does."""
    token = os.environ.get("SPACETIMEDB_TOKEN", "")
    if token:
        return token
    # Fallback: try cli.toml
    from pathlib import Path
    cli_toml = Path.home() / ".config" / "spacetime" / "cli.toml"
    if cli_toml.exists():
        import re
        content = cli_toml.read_text()
        match = re.search(r'spacetimedb_token\s*=\s*"([^"]+)"', content)
        if match:
            return match.group(1)
    return ""


# ===========================================================================
# Layer 1: Token presence
# ===========================================================================

class TestLayer1_TokenPresence(unittest.TestCase):
    """Verify the SPACETIMEDB_TOKEN is available in this environment."""

    def test_token_env_var_is_set(self):
        """SPACETIMEDB_TOKEN env var should be non-empty."""
        token = os.environ.get("SPACETIMEDB_TOKEN", "")
        self.assertTrue(
            len(token) > 0,
            "SPACETIMEDB_TOKEN env var is not set. "
            "See docs/fix-spacetimedb-token.md for setup instructions."
        )

    def test_token_looks_like_jwt(self):
        """Token should be a JWT (eyJ...)."""
        token = _get_token()
        if not token:
            self.skipTest("No token available — skipping format check")
        self.assertTrue(
            token.startswith("eyJ"),
            f"Token doesn't look like a JWT. Got: {token[:20]}..."
        )

    def test_token_resolves_from_any_source(self):
        """Token should be resolvable from env var OR cli.toml."""
        token = _get_token()
        self.assertTrue(
            len(token) > 0,
            "No SpacetimeDB token found in SPACETIMEDB_TOKEN env var "
            "or ~/.config/spacetime/cli.toml"
        )


# ===========================================================================
# Layer 2: Direct SpacetimeDB connectivity
# ===========================================================================

class TestLayer2_SpacetimeDBDirect(unittest.TestCase):
    """Verify we can reach SpacetimeDB directly and authenticate."""

    def setUp(self):
        self.token = _get_token()
        if not self.token:
            self.skipTest("No SpacetimeDB token available")

    def test_spacetimedb_is_reachable(self):
        """SpacetimeDB HTTP API should respond."""
        try:
            resp = httpx.get(f"{SPACETIMEDB_URL}/database/ping", timeout=5.0)
            # SpacetimeDB may not have a /ping endpoint, but we should get a response
            # Even a 404 means the server is up
            self.assertIn(
                resp.status_code, [200, 404, 400],
                f"SpacetimeDB not reachable at {SPACETIMEDB_URL}: {resp.status_code}"
            )
        except httpx.ConnectError:
            self.fail(f"Cannot connect to SpacetimeDB at {SPACETIMEDB_URL}")

    def test_sql_query_with_token(self):
        """SQL query should succeed with valid token."""
        url = f"{SPACETIMEDB_URL}/v1/database/{SPACETIMEDB_MODULE}/sql"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        try:
            resp = httpx.post(url, headers=headers, content="SELECT 1", timeout=10.0)
            self.assertEqual(
                resp.status_code, 200,
                f"SQL query failed ({resp.status_code}): {resp.text}"
            )
        except httpx.ConnectError:
            self.fail(f"Cannot connect to SpacetimeDB at {url}")

    def test_can_query_work_plans_table(self):
        """Should be able to query the work_plans table."""
        url = f"{SPACETIMEDB_URL}/v1/database/{SPACETIMEDB_MODULE}/sql"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        try:
            resp = httpx.post(
                url, headers=headers,
                content="SELECT * FROM work_plans LIMIT 1",
                timeout=10.0,
            )
            self.assertEqual(
                resp.status_code, 200,
                f"work_plans query failed ({resp.status_code}): {resp.text}"
            )
        except httpx.ConnectError:
            self.fail(f"Cannot connect to SpacetimeDB at {url}")


# ===========================================================================
# Layer 3: Gateway connectivity
# ===========================================================================

class TestLayer3_GatewayConnectivity(unittest.TestCase):
    """Verify the Gateway is reachable and has a valid SpacetimeDB token."""

    def test_gateway_health(self):
        """Gateway /health endpoint should respond."""
        try:
            resp = httpx.get(f"{GATEWAY_URL}/health", timeout=5.0)
            self.assertEqual(
                resp.status_code, 200,
                f"Gateway health check failed: {resp.status_code}"
            )
        except httpx.ConnectError:
            self.fail(
                f"Cannot connect to Gateway at {GATEWAY_URL}. "
                "Is the gateway running?"
            )

    def test_gateway_has_spacetimedb_token(self):
        """Gateway /api/v1/spacetimedb/token should return a non-empty token.

        The gateway exposes this endpoint at GET /api/v1/spacetimedb/token.
        It returns {"token": "eyJ..."} when SPACETIMEDB_TOKEN is configured.
        """
        try:
            resp = httpx.get(f"{GATEWAY_URL}/api/v1/spacetimedb/token", timeout=5.0)
            # Gateway returns 200 with {"token": "eyJ..."} when configured,
            # or 404 with {"error": "No SpacetimeDB token configured"} when not.
            if resp.status_code == 404:
                data = resp.json()
                self.fail(
                    f"Gateway has no SpacetimeDB token: {data.get('error', resp.text)}. "
                    "Set SPACETIMEDB_TOKEN env var for the Gateway process."
                )
            self.assertEqual(
                resp.status_code, 200,
                f"Gateway token endpoint returned {resp.status_code}: {resp.text}"
            )
            data = resp.json()
            token = data.get("token", "")
            self.assertTrue(
                len(token) > 0,
                "Gateway returned empty SpacetimeDB token. "
                "Set SPACETIMEDB_TOKEN env var for the Gateway process."
            )
        except httpx.ConnectError:
            self.fail(f"Cannot connect to Gateway at {GATEWAY_URL}")


# ===========================================================================
# Layer 4: Gateway → SpacetimeDB (plans CRUD)
# ===========================================================================

class TestLayer4_GatewayPlans(unittest.TestCase):
    """Verify the Gateway can perform plan operations via SpacetimeDB.

    This is the exact flow that fails when work_plan() tool calls error out.
    """

    TEST_PLAN_ID = None  # Set during create, used for cleanup

    def test_create_and_delete_plan(self):
        """Full round-trip: create plan → verify → delete."""
        # CREATE — agent_id is required by the Gateway
        try:
            resp = httpx.post(
                f"{GATEWAY_URL}/api/v1/plans",
                json={
                    "title": "__connectivity_test__",
                    "agent_id": "test-connectivity",
                    "description": "Auto-test, safe to delete",
                },
                timeout=10.0,
            )
        except httpx.ConnectError:
            self.fail(f"Cannot connect to Gateway at {GATEWAY_URL}")

        self.assertIn(
            resp.status_code, [200, 201],
            f"Plan creation failed ({resp.status_code}): {resp.text}. "
            "This likely means SPACETIMEDB_TOKEN is not set for the Gateway."
        )

        data = resp.json()
        plan_id = data.get("plan_id")
        self.assertTrue(plan_id, f"No plan_id in response: {data}")

        # CLEANUP — delete the test plan
        try:
            del_resp = httpx.delete(
                f"{GATEWAY_URL}/api/v1/plans/{plan_id}",
                timeout=10.0,
            )
            # Don't fail the test on cleanup errors, but log them
            if del_resp.status_code != 200:
                print(f"WARNING: Failed to delete test plan {plan_id}: {del_resp.text}")
        except Exception as e:
            print(f"WARNING: Cleanup failed for plan {plan_id}: {e}")


# ===========================================================================
# Layer 5: Full E2E — Agent sandbox → Gateway → SpacetimeDB
# ===========================================================================

class TestLayer5_AgentPersistenceClient(unittest.TestCase):
    """Verify the PersistenceClient (used by agent workers) can reach Gateway."""

    def test_persistence_client_detects_api_mode(self):
        """PersistenceClient should auto-detect 'api' mode when Gateway is up."""
        try:
            from app.agent.persistence_client import PersistenceClient

            client = PersistenceClient(agent_id="test-connectivity")

            async def _check():
                await client.init()
                return client.mode

            mode = asyncio.get_event_loop().run_until_complete(_check())
            self.assertEqual(
                mode, "api",
                f"PersistenceClient detected mode={mode}, expected 'api'. "
                "Gateway may not be reachable from this container."
            )
        except ImportError:
            self.skipTest("PersistenceClient not importable from this context")
        except Exception as e:
            if "Gateway unreachable" in str(e) or "ConnectError" in str(e):
                self.fail(
                    f"PersistenceClient cannot reach Gateway: {e}. "
                    "Check BOND_GATEWAY_URL or network connectivity."
                )
            raise


# ===========================================================================
# Layer 6: Worker.py — response.choices guard
# ===========================================================================

class TestLayer6_WorkerChoicesGuard(unittest.TestCase):
    """Verify worker.py handles empty response.choices gracefully.

    The IndexError at worker.py:948 (response.choices[0]) crashes the agent
    when the LLM returns an empty choices list. This can happen due to:
    - Rate limiting
    - Content filtering
    - Network issues
    - Malformed request (e.g., too many tokens)
    """

    def test_worker_has_choices_guard(self):
        """worker.py should check len(response.choices) before accessing [0]."""
        worker_path = os.path.join(
            os.path.dirname(__file__), "..", "backend", "app", "worker.py"
        )
        if not os.path.exists(worker_path):
            # Try from workspace root
            worker_path = "bond/backend/app/worker.py"
        if not os.path.exists(worker_path):
            self.skipTest("Cannot find worker.py")

        with open(worker_path) as f:
            content = f.read()

        # Look for the dangerous pattern: direct access without guard
        has_unguarded_access = "response.choices[0]" in content
        has_guard = (
            "len(response.choices)" in content
            or "not response.choices" in content
            or "if response.choices" in content
            or "response.choices is None" in content
        )

        if has_unguarded_access and not has_guard:
            self.fail(
                "worker.py accesses response.choices[0] without checking "
                "if choices is non-empty. This causes IndexError when the LLM "
                "returns an empty response (rate limit, content filter, etc.). "
                "Add: if not response.choices: <handle error>"
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
