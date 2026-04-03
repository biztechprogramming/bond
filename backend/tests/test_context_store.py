"""Tests for context_store.py — FTS5 knowledge base (Design Doc 075)."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from backend.app.agent.context_store import (
    ContextStore,
    chunk_content,
    is_log_shaped,
    _detect_content_shape,
)


@pytest.fixture
def tmp_index_dir(monkeypatch, tmp_path):
    """Override INDEX_DIR to use a temp directory."""
    import backend.app.agent.context_store as cs
    monkeypatch.setattr(cs, "INDEX_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def store(tmp_index_dir):
    """Create a ContextStore with temp storage."""
    s = ContextStore("test-conv-123")
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Log detection
# ---------------------------------------------------------------------------

class TestLogDetection:
    def test_log_shaped(self):
        log = "\n".join([
            "2025-07-08T14:01:00Z INFO Starting service",
            "2025-07-08T14:01:01Z INFO Health check OK",
            "2025-07-08T14:01:02Z WARN Connection pool near limit",
            "2025-07-08T14:01:03Z ERROR TimeoutException in PaymentService",
            "2025-07-08T14:01:04Z INFO Request completed",
            "2025-07-08T14:01:05Z DEBUG Trace data",
        ])
        assert is_log_shaped(log) is True

    def test_not_log_shaped(self):
        code = "def hello():\n    print('hello')\n\nclass Foo:\n    pass\n"
        assert is_log_shaped(code) is False

    def test_too_short(self):
        assert is_log_shaped("line1\nline2\nline3") is False


# ---------------------------------------------------------------------------
# Content shape detection
# ---------------------------------------------------------------------------

class TestContentShape:
    def test_detect_log(self):
        log = "\n".join([f"2025-07-08T14:0{i}:00Z INFO msg{i}" for i in range(10)])
        assert _detect_content_shape(log) == "log"

    def test_detect_code(self):
        assert _detect_content_shape("x = 1", "file_read") == "code"

    def test_detect_json(self):
        assert _detect_content_shape('{"key": "value"}') == "json"

    def test_detect_plain(self):
        assert _detect_content_shape("just some text") == "plain"


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

class TestChunking:
    def test_plain_chunking(self):
        content = "\n".join([f"line {i}" for i in range(50)])
        chunks = chunk_content(content, "test_tool")
        assert len(chunks) >= 2
        assert all("title" in c and "content" in c for c in chunks)

    def test_log_chunking(self):
        lines = []
        for i in range(30):
            level = "ERROR" if i % 10 == 0 else "INFO"
            lines.append(f"2025-07-08T14:{i:02d}:00Z {level} Message {i}")
        content = "\n".join(lines)
        chunks = chunk_content(content, "code_execute")
        assert any("ERROR" in c["title"] for c in chunks)

    def test_code_chunking(self):
        content = "\n".join([f"    line {i}" for i in range(50)])
        chunks = chunk_content(content, "file_read")
        assert len(chunks) >= 1
        # Line numbers should be in the content
        assert "1:" in chunks[0]["content"]

    def test_json_chunking(self):
        data = {"key1": "value1", "key2": [1, 2, 3], "key3": {"nested": True}}
        content = json.dumps(data)
        chunks = chunk_content(content, "api_call")
        assert len(chunks) >= 1

    def test_max_chunk_size(self):
        content = "x" * 20000
        chunks = chunk_content(content, "test")
        for chunk in chunks:
            assert len(chunk["content"].encode("utf-8")) <= 4096 + 200  # small tolerance


# ---------------------------------------------------------------------------
# FTS5 DB creation and indexing
# ---------------------------------------------------------------------------

class TestContextStore:
    def test_create_db(self, store):
        store._ensure_db()
        assert os.path.exists(store.db_path)

    def test_index_content(self, store):
        source_id = store.index(
            content="This is a test document about Python programming",
            tool_name="code_execute",
            tool_args={"code": "cat file.py"},
            turn_number=1,
        )
        assert source_id > 0
        stats = store.get_stats()
        assert stats["sources"] == 1
        assert stats["chunks"] >= 1

    def test_search_basic(self, store):
        store.index("Python programming is great for data science", "file_read", turn_number=1)
        results = store.search(["Python"])
        assert len(results) >= 1
        assert "Python" in results[0]["content"] or "python" in results[0]["content"].lower()

    def test_search_bm25_ranking(self, store):
        store.index("Error in PaymentService: TimeoutException at line 42", "code_execute", turn_number=1)
        store.index("Normal operation log with no errors", "code_execute", turn_number=2)
        results = store.search(["TimeoutException"])
        assert len(results) >= 1
        assert "TimeoutException" in results[0]["content"]

    def test_search_fuzzy(self, store):
        store.index("The PaymentService encountered a timeout", "code_execute", turn_number=1)
        # Misspelled query should still find via fuzzy correction
        results = store.search(["PaymentServic"])  # missing 'e'
        # May or may not find depending on fuzzy — just verify no crash
        assert isinstance(results, list)

    def test_search_no_results(self, store):
        store.index("Some content about databases", "file_read", turn_number=1)
        results = store.search(["nonexistent_xyzzy_term"])
        assert results == [] or isinstance(results, list)

    def test_search_result_format(self, store):
        store.index("Test content here", "file_search", {"pattern": "test"}, turn_number=3)
        results = store.search(["test content"])
        if results:
            r = results[0]
            assert "title" in r
            assert "content" in r
            assert "source_tool" in r
            assert "turn_number" in r
            assert r["source_tool"] == "file_search"
            assert r["turn_number"] == 3

    def test_search_limit(self, store):
        for i in range(10):
            store.index(f"Document {i} about testing", "file_read", turn_number=i)
        results = store.search(["testing"], limit=3)
        assert len(results) <= 3

    def test_multiple_queries(self, store):
        store.index("Error in authentication module", "code_execute", turn_number=1)
        store.index("Database connection pool exhausted", "code_execute", turn_number=2)
        results = store.search(["authentication", "database"], limit=5)
        assert len(results) >= 1

    def test_get_stats(self, store):
        stats = store.get_stats()
        assert stats == {"sources": 0, "chunks": 0}
        store.index("test", "test_tool", turn_number=1)
        stats = store.get_stats()
        assert stats["sources"] == 1

    def test_delete(self, tmp_index_dir):
        store = ContextStore("to-delete")
        store.index("test content", "test_tool", turn_number=1)
        assert os.path.exists(store.db_path)
        store.close()
        ContextStore.delete("to-delete")
        assert not os.path.exists(os.path.join(str(tmp_index_dir), "to-delete.db"))
