"""Tests for the Permission Broker client."""

from __future__ import annotations

import json
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from typing import Any

import pytest
import pytest_asyncio

from backend.app.agent.broker_client import BrokerClient, BrokerError


class MockBrokerHandler(BaseHTTPRequestHandler):
    """Mock broker HTTP handler."""

    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_len)) if content_len else {}

        if self.path == "/exec":
            command = body.get("command", "")
            if "deny-this" in command:
                self._respond(200, {
                    "status": "denied",
                    "decision": "deny",
                    "reason": "Command denied by policy",
                    "policy_rule": "default#rule-test",
                })
            else:
                self._respond(200, {
                    "status": "ok",
                    "decision": "allow",
                    "exit_code": 0,
                    "stdout": f"executed: {command}",
                    "stderr": "",
                    "duration_ms": 42,
                })
        elif self.path == "/token/renew":
            self._respond(200, {"token": "new-mock-token"})
        else:
            self._respond(404, {"error": "not found"})

    def _respond(self, status: int, data: dict[str, Any]):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        pass  # suppress logs


@pytest.fixture(scope="module")
def mock_server():
    server = HTTPServer(("127.0.0.1", 0), MockBrokerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.mark.asyncio
async def test_exec_allowed(mock_server: str):
    client = BrokerClient(base_url=mock_server, token="test-token")
    try:
        result = await client.exec("echo hello")
        assert result["decision"] == "allow"
        assert result["exit_code"] == 0
        assert "echo hello" in result["stdout"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_exec_denied(mock_server: str):
    client = BrokerClient(base_url=mock_server, token="test-token")
    try:
        with pytest.raises(BrokerError) as exc_info:
            await client.exec("deny-this-command")
        assert exc_info.value.decision == "deny"
        assert exc_info.value.policy_rule == "default#rule-test"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_broker_error_attributes():
    err = BrokerError("test error", decision="deny", policy_rule="default#rule-0")
    assert str(err) == "test error"
    assert err.decision == "deny"
    assert err.policy_rule == "default#rule-0"


@pytest.mark.asyncio
async def test_renew_token(mock_server: str):
    client = BrokerClient(base_url=mock_server, token="old-token")
    try:
        new_token = await client.renew_token()
        assert new_token == "new-mock-token"
        assert client.token == "new-mock-token"
    finally:
        await client.close()
