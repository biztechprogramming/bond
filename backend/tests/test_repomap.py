"""Tests for the repomap module."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ── Tag extraction tests ──


def test_extract_tags_python_snippet():
    """Tag extraction from a small Python snippet produces defs and refs."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")

    from backend.app.agent.repomap.tags import extract_tags

    code = '''\
class MyClass:
    def my_method(self, x: int) -> str:
        return str(x)

def helper_function(a, b):
    obj = MyClass()
    return obj.my_method(a)
'''
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(code)
        f.flush()
        fname = f.name

    try:
        tags = extract_tags(fname, "test_file.py", code=code)
        assert len(tags) > 0

        def_names = {t.name for t in tags if t.kind == "def"}
        assert "MyClass" in def_names
        assert "my_method" in def_names
        assert "helper_function" in def_names

        ref_names = {t.name for t in tags if t.kind == "ref"}
        # MyClass() is a call reference, my_method is an attribute call reference
        assert "MyClass" in ref_names or "my_method" in ref_names

        # Signatures should be present for defs
        class_tag = next(t for t in tags if t.kind == "def" and t.name == "MyClass")
        assert "class MyClass" in class_tag.signature
    finally:
        os.unlink(fname)


def test_extract_tags_unsupported_language():
    """Unsupported file extensions return empty tags."""
    from backend.app.agent.repomap.tags import extract_tags

    tags = extract_tags("/tmp/test.xyz", "test.xyz", code="some content")
    assert tags == []


# ── PageRank scoring tests ──


def test_pagerank_produces_reasonable_rankings():
    """Files that define widely-referenced symbols score higher."""
    from backend.app.agent.repomap.ranking import rank_files
    from backend.app.agent.repomap.tags import Tag

    tags = [
        # core.py defines "CoreClass" — referenced by many
        Tag(name="CoreClass", kind="def", rel_fname="core.py", fname="/core.py", line=0, signature="class CoreClass"),
        # utils.py defines "helper" — referenced by one
        Tag(name="helper", kind="def", rel_fname="utils.py", fname="/utils.py", line=0, signature="def helper()"),
        # main.py references both
        Tag(name="CoreClass", kind="ref", rel_fname="main.py", fname="/main.py", line=1),
        Tag(name="helper", kind="ref", rel_fname="main.py", fname="/main.py", line=2),
        # worker.py references CoreClass
        Tag(name="CoreClass", kind="ref", rel_fname="worker.py", fname="/worker.py", line=1),
        # test.py references CoreClass
        Tag(name="CoreClass", kind="ref", rel_fname="test.py", fname="/test.py", line=1),
    ]

    scores = rank_files(tags)
    assert len(scores) > 0

    # core.py should rank higher than utils.py (more references to its symbols)
    assert scores.get("core.py", 0) > scores.get("utils.py", 0)


def test_pagerank_with_focus_files():
    """Focus files should get boosted scores."""
    from backend.app.agent.repomap.ranking import rank_files
    from backend.app.agent.repomap.tags import Tag

    tags = [
        Tag(name="A", kind="def", rel_fname="a.py", fname="/a.py", line=0),
        Tag(name="B", kind="def", rel_fname="b.py", fname="/b.py", line=0),
        Tag(name="A", kind="ref", rel_fname="b.py", fname="/b.py", line=1),
    ]

    scores_no_focus = rank_files(tags)
    scores_focus_b = rank_files(tags, focus_files=["b.py"])

    # b.py should rank higher when focused
    assert scores_focus_b.get("b.py", 0) > scores_no_focus.get("b.py", 0)


# ── Token-budget rendering tests ──


def test_render_respects_budget():
    """Rendering should not exceed the token budget."""
    from backend.app.agent.repomap import _render_map, _estimate_tokens
    from backend.app.agent.repomap.tags import Tag

    tags = []
    scores = {}
    for i in range(100):
        fname = f"file_{i:03d}.py"
        tags.append(Tag(
            name=f"function_{i}",
            kind="def",
            rel_fname=fname,
            fname=f"/{fname}",
            line=0,
            signature=f"def function_{i}(arg1, arg2, arg3): # a long signature to use tokens",
        ))
        scores[fname] = 100 - i  # descending importance

    budget = 500
    rendered = _render_map(tags, scores, budget)
    actual_tokens = _estimate_tokens(rendered)
    # Allow small overshoot due to token estimation granularity
    assert actual_tokens <= budget + 10


# ── Cache tests ──


def test_cache_hit_miss():
    """Cache returns None on miss and stored content on hit."""
    from backend.app.agent.repomap.cache import RepoMapCache

    with tempfile.TemporaryDirectory() as tmpdir:
        cache = RepoMapCache(cache_dir=os.path.join(tmpdir, "cache"))

        # Create a dummy file for hashing
        test_file = os.path.join(tmpdir, "test.py")
        Path(test_file).write_text("print('hello')")

        files = ["test.py"]
        budget = 1000

        # Miss
        assert cache.get(tmpdir, files, budget) is None

        # Set
        cache.set(tmpdir, files, budget, "cached content here")

        # Hit
        result = cache.get(tmpdir, files, budget)
        assert result == "cached content here"


def test_cache_eviction():
    """Cache evicts old entries when over the max."""
    from backend.app.agent.repomap.cache import RepoMapCache

    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = os.path.join(tmpdir, "cache")
        cache = RepoMapCache(cache_dir=cache_dir)

        # Create many cache entries directly
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        for i in range(25):
            (Path(cache_dir) / f"entry_{i:03d}.txt").write_text(f"content {i}")

        cache._evict_old(max_entries=20)

        remaining = list(Path(cache_dir).glob("*.txt"))
        assert len(remaining) <= 20


# ── Integration test ──


@pytest.mark.asyncio
async def test_generate_repo_map_integration():
    """generate_repo_map produces output for a small repo with Python files."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")

    from backend.app.agent.repomap import generate_repo_map

    with tempfile.TemporaryDirectory() as tmpdir:
        # Initialize a git repo
        import subprocess

        subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=tmpdir, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=tmpdir, capture_output=True,
        )

        # Create some Python files
        (Path(tmpdir) / "core.py").write_text(
            "class Engine:\n    def run(self):\n        pass\n"
        )
        (Path(tmpdir) / "main.py").write_text(
            "from core import Engine\n\ndef start():\n    e = Engine()\n    e.run()\n"
        )
        (Path(tmpdir) / "utils.py").write_text(
            "def helper(x):\n    return x + 1\n"
        )

        # Add and commit
        subprocess.run(["git", "add", "."], cwd=tmpdir, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=tmpdir, capture_output=True,
        )

        result = await generate_repo_map(tmpdir, token_budget=5000, refresh=True)

        assert len(result) > 0
        assert "core.py" in result
        assert "Engine" in result
        assert "main.py" in result


# ── Focus file extraction tests ──


def test_extract_focus_files():
    """_extract_focus_files finds file paths in user messages."""
    from backend.app.agent.pre_gather_integration import _extract_focus_files

    msg = "Please fix the bug in backend/app/worker.py and update frontend/src/index.ts"
    files = _extract_focus_files(msg)
    assert files is not None
    assert "backend/app/worker.py" in files
    assert "frontend/src/index.ts" in files


def test_extract_focus_files_none_for_no_paths():
    """_extract_focus_files returns None when no paths found."""
    from backend.app.agent.pre_gather_integration import _extract_focus_files

    assert _extract_focus_files("hello world") is None
    assert _extract_focus_files("fix the bug please") is None
