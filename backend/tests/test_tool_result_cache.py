"""Tests for tool result caching (Design Doc 065)."""

import os
import time

import pytest

from backend.app.agent.tool_result_cache import (
    TOOL_CACHE_MAX_ENTRIES,
    TOOL_CACHE_MAX_CONTENT_SIZE,
    CacheStats,
    ToolResultCache,
    _count_tokens,
)


@pytest.fixture
def cache():
    return ToolResultCache(shadow_mode=False)


@pytest.fixture
def shadow_cache():
    return ToolResultCache(shadow_mode=True)


@pytest.fixture
def sample_file(tmp_path):
    """Create a sample file for testing."""
    p = tmp_path / "sample.py"
    p.write_text("import os\nfrom pathlib import Path\n\nclass Worker:\n    def __init__(self):\n        pass\n")
    return p


# --- Basic hit/miss ---

def test_cache_miss_on_first_read(cache, sample_file):
    args = {"path": str(sample_file)}
    result = cache.check("file_read", args, turn=1)
    assert result is None
    assert cache.stats.misses == 1


def test_cache_hit_after_store(cache, sample_file):
    args = {"path": str(sample_file)}
    content = sample_file.read_text()
    cache.store("file_read", args, content, turn=1)

    result = cache.check("file_read", args, turn=2)
    assert result is not None
    assert result.content == content
    assert cache.stats.hits == 1


def test_non_cacheable_tool_ignored(cache):
    cache.store("file_search", {"pattern": "foo"}, "result", turn=1)
    result = cache.check("file_search", {"pattern": "foo"}, turn=2)
    assert result is None


# --- Staleness ---

def test_stale_after_mtime_change(cache, sample_file):
    args = {"path": str(sample_file)}
    cache.store("file_read", args, sample_file.read_text(), turn=1)

    # Modify the file (change mtime)
    time.sleep(0.05)
    sample_file.write_text("modified content\n")

    result = cache.check("file_read", args, turn=2)
    assert result is None
    assert cache.stats.misses == 1


def test_file_deleted_returns_miss(cache, sample_file):
    args = {"path": str(sample_file)}
    cache.store("file_read", args, sample_file.read_text(), turn=1)
    sample_file.unlink()

    result = cache.check("file_read", args, turn=2)
    assert result is None


# --- Force bypass ---

def test_force_bypasses_cache(cache, sample_file):
    args = {"path": str(sample_file)}
    cache.store("file_read", args, sample_file.read_text(), turn=1)

    result = cache.check("file_read", {**args, "force": True}, turn=2)
    assert result is None
    # force bypass doesn't count as a miss
    assert cache.stats.misses == 0


# --- LRU eviction ---

def test_lru_eviction(cache, tmp_path):
    # Fill cache beyond max entries
    for i in range(TOOL_CACHE_MAX_ENTRIES + 5):
        p = tmp_path / f"file_{i}.txt"
        p.write_text(f"content {i}")
        cache.store("file_read", {"path": str(p)}, f"content {i}", turn=1)

    assert len(cache._cache) == TOOL_CACHE_MAX_ENTRIES

    # The first 5 files should have been evicted
    p0 = tmp_path / "file_0.txt"
    result = cache.check("file_read", {"path": str(p0)}, turn=2)
    assert result is None

    # The last file should still be cached
    p_last = tmp_path / f"file_{TOOL_CACHE_MAX_ENTRIES + 4}.txt"
    result = cache.check("file_read", {"path": str(p_last)}, turn=2)
    assert result is not None


# --- Max content size ---

def test_large_content_not_cached(cache, sample_file):
    args = {"path": str(sample_file)}
    big_content = "x" * (TOOL_CACHE_MAX_CONTENT_SIZE + 1)
    cache.store("file_read", args, big_content, turn=1)

    result = cache.check("file_read", args, turn=2)
    assert result is None


# --- Partial range reads ---

def test_partial_range_from_cached_full(cache, sample_file):
    content = sample_file.read_text()
    cache.store("file_read", {"path": str(sample_file)}, content, turn=1)

    # Request lines 2-3
    result = cache.check("file_read", {"path": str(sample_file), "line_start": 2, "line_end": 3}, turn=2)
    assert result is not None
    lines = content.splitlines(keepends=True)
    expected = "".join(lines[1:3])
    assert result.content == expected


# --- Cache key: file tools keyed by resolved path ---

def test_file_tools_keyed_by_path(cache, sample_file):
    content = sample_file.read_text()
    # Store with "path" key
    cache.store("file_read", {"path": str(sample_file)}, content, turn=1)

    # Check with "file_path" key (same resolved path)
    result = cache.check("file_read", {"file_path": str(sample_file)}, turn=2)
    assert result is not None


# --- record_mutation and diff response ---

def test_mutation_triggers_diff_response(cache, sample_file):
    content = sample_file.read_text()
    args = {"path": str(sample_file)}
    cache.store("file_read", args, content, turn=1)

    # Agent edits the file
    time.sleep(0.05)
    new_content = content + "    def run(self):\n        return True\n"
    sample_file.write_text(new_content)
    cache.record_mutation("file_edit", args, turn=2)

    # Re-read: cache check returns miss because mtime changed
    result = cache.check("file_read", args, turn=3)
    assert result is None  # stale due to mtime change

    # But if we store the new content and then check with an older cached version,
    # let's test format_cache_hit directly
    from backend.app.agent.tool_result_cache import CachedToolResult
    from datetime import datetime, timezone

    cached = CachedToolResult(
        tool_name="file_read",
        args_hash="file_read:" + str(sample_file.resolve()),
        resolved_path=str(sample_file.resolve()),
        content=content,  # old content
        token_count=_count_tokens(content),
        fingerprint="old",
        turn_number=1,
        timestamp=datetime.now(timezone.utc),
    )
    cache.record_mutation("file_edit", {"path": str(sample_file)}, turn=2)

    formatted = cache.format_cache_hit(cached, current_turn=3)
    assert formatted is not None
    assert "MODIFIED" in formatted
    assert "turn 2" in formatted


def test_unchanged_format(cache, sample_file):
    content = sample_file.read_text()
    args = {"path": str(sample_file)}
    cache.store("file_read", args, content, turn=1)

    cached = cache.check("file_read", args, turn=2)
    assert cached is not None

    formatted = cache.format_cache_hit(cached, current_turn=2)
    assert "UNCHANGED" in formatted
    assert "First 5 lines:" in formatted
    assert "force=true" in formatted


# --- revalidate_after_execute ---

def test_revalidate_drops_changed_files(cache, tmp_path):
    f1 = tmp_path / "a.py"
    f2 = tmp_path / "b.py"
    f1.write_text("aaa")
    f2.write_text("bbb")

    cache.store("file_read", {"path": str(f1)}, "aaa", turn=1)
    cache.store("file_read", {"path": str(f2)}, "bbb", turn=1)

    # Modify f1
    time.sleep(0.05)
    f1.write_text("aaa modified")

    cache.revalidate_after_execute()

    # f1 should be gone, f2 should remain
    assert cache.check("file_read", {"path": str(f1)}, turn=2) is None
    result = cache.check("file_read", {"path": str(f2)}, turn=2)
    assert result is not None


# --- Shadow mode ---

def test_shadow_mode_logs_but_returns_none(shadow_cache, sample_file):
    args = {"path": str(sample_file)}
    content = sample_file.read_text()
    shadow_cache.store("file_read", args, content, turn=1)

    result = shadow_cache.check("file_read", args, turn=2)
    assert result is None  # shadow mode returns None
    assert shadow_cache.stats.hits == 1  # but still counts the hit
    assert shadow_cache.stats.tokens_saved > 0


# --- CacheStats ---

def test_cache_stats_hit_rate():
    stats = CacheStats(hits=3, misses=7)
    assert stats.hit_rate == pytest.approx(0.3)


def test_cache_stats_hit_rate_zero():
    stats = CacheStats()
    assert stats.hit_rate == 0.0


# --- _count_tokens ---

def test_count_tokens():
    assert _count_tokens("hello world") == len("hello world") // 4


# --- diff_too_large tracking ---

def test_diff_too_large_tracked(cache, sample_file):
    from backend.app.agent.tool_result_cache import CachedToolResult, MAX_DIFF_LINES
    from datetime import datetime, timezone

    content = sample_file.read_text()
    # Write a very different file to produce a huge diff
    time.sleep(0.05)
    big_new = "\n".join(f"line {i}" for i in range(MAX_DIFF_LINES + 100))
    sample_file.write_text(big_new)

    cached = CachedToolResult(
        tool_name="file_read",
        args_hash="file_read:" + str(sample_file.resolve()),
        resolved_path=str(sample_file.resolve()),
        content=content,
        token_count=_count_tokens(content),
        fingerprint="old",
        turn_number=1,
        timestamp=datetime.now(timezone.utc),
    )
    cache.record_mutation("file_edit", {"path": str(sample_file)}, turn=2)

    result = cache.format_cache_hit(cached, current_turn=3)
    assert result is None
    assert cache.stats.diff_too_large == 1
