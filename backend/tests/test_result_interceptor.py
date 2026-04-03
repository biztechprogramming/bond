"""Tests for result_interceptor.py — tier routing (Design Doc 075)."""

from __future__ import annotations

import json

import pytest

from backend.app.agent.result_interceptor import (
    TIER_2_THRESHOLD,
    TIER_3_THRESHOLD,
    TIER_4_THRESHOLD,
    get_tier,
    intercept_tool_result,
    _measure_output,
    _extract_text_content,
    _is_code_file,
    get_store,
    close_store,
)


@pytest.fixture(autouse=True)
def patch_index_dir(monkeypatch, tmp_path):
    """Use temp dir for all context stores."""
    import backend.app.agent.context_store as cs
    monkeypatch.setattr(cs, "INDEX_DIR", str(tmp_path))
    yield
    # Cleanup stores
    close_store("test-conv")


# ---------------------------------------------------------------------------
# Tier classification
# ---------------------------------------------------------------------------

class TestGetTier:
    def test_tier_1(self):
        assert get_tier(100) == 1
        assert get_tier(4095) == 1

    def test_tier_2(self):
        assert get_tier(TIER_2_THRESHOLD) == 2
        assert get_tier(16 * 1024 - 1) == 2

    def test_tier_3(self):
        assert get_tier(TIER_3_THRESHOLD) == 3
        assert get_tier(64 * 1024 - 1) == 3

    def test_tier_4(self):
        assert get_tier(TIER_4_THRESHOLD) == 4
        assert get_tier(1_000_000) == 4


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------

class TestMeasure:
    def test_measure_output(self):
        result = {"content": "hello"}
        size = _measure_output(result)
        assert size == len(json.dumps(result).encode("utf-8"))

    def test_extract_text_content(self):
        assert _extract_text_content({"content": "foo"}) == "foo"
        assert _extract_text_content({"stdout": "bar"}) == "bar"
        assert "key" in _extract_text_content({"key": "val"})  # falls back to JSON


# ---------------------------------------------------------------------------
# Tier routing
# ---------------------------------------------------------------------------

class TestIsCodeFile:
    def test_python_file(self):
        assert _is_code_file("file_read", {"path": "src/main.py"}) is True

    def test_typescript_file(self):
        assert _is_code_file("file_read", {"file_path": "app/index.tsx"}) is True

    def test_yaml_file(self):
        assert _is_code_file("file_read", {"path": "config.yaml"}) is True

    def test_non_file_tool(self):
        assert _is_code_file("code_execute", {"path": "main.py"}) is False

    def test_unknown_extension(self):
        assert _is_code_file("file_read", {"path": "data.parquet"}) is False

    def test_dockerfile_no_extension(self):
        assert _is_code_file("file_read", {"path": "Dockerfile"}) is True

    def test_no_path(self):
        assert _is_code_file("file_read", {}) is False


class TestInterceptToolResult:
    @pytest.mark.asyncio
    async def test_tier_1_passthrough(self):
        result = {"content": "small"}
        out, indexed = await intercept_tool_result(
            "file_read", {}, result, "test-conv",
        )
        assert out == result
        assert indexed is False

    @pytest.mark.asyncio
    async def test_tier_2_passthrough_with_index(self):
        # Create content that's 4KB-16KB
        content = "x" * (5 * 1024)
        result = {"content": content}
        out, indexed = await intercept_tool_result(
            "code_execute", {"code": "cat file"}, result, "test-conv",
        )
        assert indexed is True
        assert out["_indexed"] is True
        # Content is still present (passthrough)
        assert out["content"] == content

    @pytest.mark.asyncio
    async def test_tier_3_summary(self):
        content = "x" * (20 * 1024)
        result = {"content": content}
        out, indexed = await intercept_tool_result(
            "code_execute", {"code": "tail -n 500 log.txt"}, result, "test-conv",
        )
        assert indexed is True
        assert out["_indexed"] is True
        assert "_summary" in out
        assert "📋" in out["_summary"]
        assert "_search_hint" in out
        # Original content should NOT be in the summary result
        assert "content" not in out or out.get("content") != content

    @pytest.mark.asyncio
    async def test_tier_4_warning(self):
        content = "x" * (70 * 1024)
        result = {"content": content}
        out, indexed = await intercept_tool_result(
            "code_execute", {"code": "cat huge.sql"}, result, "test-conv",
        )
        assert indexed is True
        assert out["_tier"] == 4
        assert "⚠️" in out["_summary"]
        assert "_warning" in out

    @pytest.mark.asyncio
    async def test_raw_parameter(self):
        content = "x" * (20 * 1024)
        result = {"content": content}
        out, indexed = await intercept_tool_result(
            "code_execute", {}, result, "test-conv", raw=True,
        )
        assert indexed is True
        assert out["_indexed"] is True
        # With raw=True, content should still be present
        assert out["content"] == content
        # No summary
        assert "_summary" not in out

    @pytest.mark.asyncio
    async def test_code_file_gets_indexed_code_flag(self):
        """Code files should get _indexed_code=True alongside _indexed."""
        content = "x" * (5 * 1024)
        result = {"content": content}
        out, indexed = await intercept_tool_result(
            "file_read", {"path": "src/main.py"}, result, "test-conv",
        )
        assert indexed is True
        assert out["_indexed"] is True
        assert out["_indexed_code"] is True

    @pytest.mark.asyncio
    async def test_non_code_file_no_indexed_code_flag(self):
        """Non-code tool results should NOT get _indexed_code."""
        content = "x" * (5 * 1024)
        result = {"content": content}
        out, indexed = await intercept_tool_result(
            "code_execute", {"code": "echo hi"}, result, "test-conv",
        )
        assert indexed is True
        assert out["_indexed"] is True
        assert "_indexed_code" not in out

    @pytest.mark.asyncio
    async def test_indexed_flag_on_tier_2(self):
        content = "y" * (5 * 1024)
        result = {"stdout": content}
        out, indexed = await intercept_tool_result(
            "code_execute", {}, result, "test-conv",
        )
        assert out.get("_indexed") is True
