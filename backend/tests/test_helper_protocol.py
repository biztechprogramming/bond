"""Tests for Phase 2: Persistent helper process and helper protocol.

Tests the bond_helper.py (in-container script) and helper_protocol.py
(backend manager) in isolation without requiring Docker.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Tests for bond_helper.py (the in-container script)
# ---------------------------------------------------------------------------

# Import the helper module directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "sandbox"))
from bond_helper import dispatch, handle_file_read, handle_file_stat, handle_batch, handle_ping


class TestBondHelperFileRead:
    """Test the helper's file_read method."""

    def test_read_existing_file(self, tmp_path):
        content = "line1\nline2\nline3\n"
        f = tmp_path / "test.txt"
        f.write_text(content)

        result = handle_file_read({"path": str(f)})
        assert "result" in result
        assert result["result"]["content"] == content
        assert result["result"]["total_lines"] == 3  # splitlines doesn't add trailing empty
        assert result["result"]["mtime"] > 0
        assert result["result"]["size"] > 0

    def test_read_nonexistent_file(self):
        result = handle_file_read({"path": "/nonexistent/file.txt"})
        assert "error" in result
        assert "not found" in result["error"]["message"].lower()

    def test_read_with_line_range(self, tmp_path):
        lines = [f"line{i}" for i in range(1, 11)]
        f = tmp_path / "lines.txt"
        f.write_text("\n".join(lines))

        result = handle_file_read({"path": str(f), "line_start": 3, "line_end": 5})
        assert "result" in result
        r = result["result"]
        assert r["line_start"] == 3
        assert r["line_end"] == 5
        assert "line3" in r["content"]
        assert "line5" in r["content"]
        assert "line6" not in r["content"]

    def test_read_missing_path(self):
        result = handle_file_read({})
        assert "error" in result

    def test_read_directory(self, tmp_path):
        result = handle_file_read({"path": str(tmp_path)})
        assert "error" in result
        assert "not a file" in result["error"]["message"].lower()


class TestBondHelperFileStat:
    """Test the helper's file_stat method."""

    def test_stat_existing_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello")

        result = handle_file_stat({"path": str(f)})
        assert "result" in result
        assert result["result"]["exists"] is True
        assert result["result"]["is_file"] is True
        assert result["result"]["size"] == 5

    def test_stat_nonexistent(self):
        result = handle_file_stat({"path": "/nonexistent"})
        assert "result" in result
        assert result["result"]["exists"] is False


class TestBondHelperBatch:
    """Test the helper's batch method."""

    def test_batch_multiple_reads(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("content_a")
        f2.write_text("content_b")

        result = handle_batch({
            "calls": [
                {"method": "file_read", "params": {"path": str(f1)}},
                {"method": "file_read", "params": {"path": str(f2)}},
            ]
        })
        assert "result" in result
        results = result["result"]
        assert len(results) == 2
        assert results[0]["result"]["content"] == "content_a"
        assert results[1]["result"]["content"] == "content_b"

    def test_batch_with_error(self, tmp_path):
        f1 = tmp_path / "exists.txt"
        f1.write_text("hello")

        result = handle_batch({
            "calls": [
                {"method": "file_read", "params": {"path": str(f1)}},
                {"method": "file_read", "params": {"path": "/nonexistent"}},
            ]
        })
        results = result["result"]
        assert "result" in results[0]
        assert "error" in results[1]

    def test_batch_unknown_method(self):
        result = handle_batch({
            "calls": [{"method": "nonexistent_method", "params": {}}]
        })
        assert "error" in result["result"][0]

    def test_batch_empty(self):
        result = handle_batch({"calls": []})
        assert "error" in result


class TestBondHelperDispatch:
    """Test the JSON-RPC dispatch."""

    def test_dispatch_with_id(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello")

        response = dispatch({"id": 42, "method": "file_read", "params": {"path": str(f)}})
        assert response["id"] == 42
        assert "result" in response

    def test_dispatch_unknown_method(self):
        response = dispatch({"id": 1, "method": "unknown"})
        assert "error" in response

    def test_dispatch_ping(self):
        response = dispatch({"id": 1, "method": "ping", "params": {}})
        assert response["result"]["status"] == "ok"
        assert "pid" in response["result"]


# ---------------------------------------------------------------------------
# Tests for helper_protocol.py (the backend manager)
# ---------------------------------------------------------------------------

class TestHelperProtocolIntegration:
    """Integration test: start the helper as a subprocess (no Docker needed)."""

    @pytest.mark.asyncio
    async def test_helper_subprocess_communication(self, tmp_path):
        """Start bond_helper.py as a local subprocess and communicate with it."""
        helper_script = os.path.join(
            os.path.dirname(__file__), "..", "app", "sandbox", "bond_helper.py"
        )

        proc = await asyncio.create_subprocess_exec(
            sys.executable, helper_script,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            # Read the "ready" message
            raw = await asyncio.wait_for(proc.stdout.readline(), timeout=5)
            ready = json.loads(raw.decode())
            assert ready["ready"] is True

            # Send a ping
            request = json.dumps({"id": 1, "method": "ping", "params": {}}) + "\n"
            proc.stdin.write(request.encode())
            await proc.stdin.drain()

            raw = await asyncio.wait_for(proc.stdout.readline(), timeout=5)
            response = json.loads(raw.decode())
            assert response["id"] == 1
            assert response["result"]["status"] == "ok"

            # Send a file_read
            test_file = tmp_path / "hello.txt"
            test_file.write_text("hello world\nsecond line\n")

            request = json.dumps({
                "id": 2,
                "method": "file_read",
                "params": {"path": str(test_file)},
            }) + "\n"
            proc.stdin.write(request.encode())
            await proc.stdin.drain()

            raw = await asyncio.wait_for(proc.stdout.readline(), timeout=5)
            response = json.loads(raw.decode())
            assert response["id"] == 2
            assert "hello world" in response["result"]["content"]
            assert response["result"]["total_lines"] == 2

            # Send a batch
            f1 = tmp_path / "a.py"
            f2 = tmp_path / "b.py"
            f1.write_text("import os\n")
            f2.write_text("import sys\n")

            request = json.dumps({
                "id": 3,
                "method": "batch",
                "params": {
                    "calls": [
                        {"method": "file_read", "params": {"path": str(f1)}},
                        {"method": "file_read", "params": {"path": str(f2)}},
                    ]
                },
            }) + "\n"
            proc.stdin.write(request.encode())
            await proc.stdin.drain()

            raw = await asyncio.wait_for(proc.stdout.readline(), timeout=5)
            response = json.loads(raw.decode())
            assert response["id"] == 3
            results = response["result"]
            assert len(results) == 2
            assert "import os" in results[0]["result"]["content"]
            assert "import sys" in results[1]["result"]["content"]

        finally:
            proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                proc.kill()

    @pytest.mark.asyncio
    async def test_helper_handles_invalid_json(self):
        """Helper should return error for invalid JSON, not crash."""
        helper_script = os.path.join(
            os.path.dirname(__file__), "..", "app", "sandbox", "bond_helper.py"
        )

        proc = await asyncio.create_subprocess_exec(
            sys.executable, helper_script,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            # Read ready
            await asyncio.wait_for(proc.stdout.readline(), timeout=5)

            # Send invalid JSON
            proc.stdin.write(b"not json at all\n")
            await proc.stdin.drain()

            raw = await asyncio.wait_for(proc.stdout.readline(), timeout=5)
            response = json.loads(raw.decode())
            assert "error" in response

            # Helper should still be alive — send a valid ping
            request = json.dumps({"id": 99, "method": "ping", "params": {}}) + "\n"
            proc.stdin.write(request.encode())
            await proc.stdin.drain()

            raw = await asyncio.wait_for(proc.stdout.readline(), timeout=5)
            response = json.loads(raw.decode())
            assert response["id"] == 99
            assert response["result"]["status"] == "ok"

        finally:
            proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                proc.kill()
