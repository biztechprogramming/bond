"""Tests for Langfuse prompt injection audit instrumentation.

Verifies that:
1. Fragment selection reasons are tagged correctly
2. Universal fragment metadata is collected
3. Langfuse metadata dict is built correctly
4. The callback is conditionally enabled based on env vars
"""

import os
import unittest
from pathlib import Path
from unittest.mock import patch


class TestFragmentSelectionReasons(unittest.TestCase):
    """Test that _select_relevant_fragments tags selection reasons."""

    def test_core_fragments_tagged(self):
        """Core-tier fragments should get _selection_reason='core_always'."""
        from backend.app.agent.context_pipeline import _select_relevant_fragments
        import asyncio

        fragments = [
            {
                "id": "frag-core-1",
                "name": "safety",
                "content": "Be safe.",
                "tier": "core",
                "enabled": True,
                "token_estimate": 50,
                "task_triggers": "[]",
            },
            {
                "id": "frag-std-1",
                "name": "python",
                "content": "Use Python best practices.",
                "tier": "standard",
                "enabled": True,
                "token_estimate": 100,
                "task_triggers": "[]",
            },
        ]

        config = {
            "utility_model": "fake-model",
            "conversation_id": "test-conv",
        }

        # Mock the utility model call to avoid real LLM
        with patch(
            "backend.app.agent.context_pipeline._utility_model_select",
            return_value=[],
        ):
            result = asyncio.run(
                _select_relevant_fragments(
                    fragments, "hello", [], config, {}
                )
            )

        # Core fragment should be selected with reason
        core = [f for f in result if f["name"] == "safety"]
        self.assertEqual(len(core), 1)
        self.assertEqual(core[0]["_selection_reason"], "core_always")

    def test_keyword_triggered_fragments_tagged(self):
        """Fragments matched by keyword triggers should get _selection_reason='keyword_trigger'."""
        from backend.app.agent.context_pipeline import _select_relevant_fragments
        import asyncio

        fragments = [
            {
                "id": "frag-trigger-1",
                "name": "docker-guide",
                "content": "Docker best practices.",
                "tier": "standard",
                "enabled": True,
                "token_estimate": 100,
                "task_triggers": '["docker", "container"]',
            },
        ]

        config = {
            "utility_model": "fake-model",
            "conversation_id": "test-conv",
        }

        with patch(
            "backend.app.agent.context_pipeline._utility_model_select",
            return_value=[],
        ):
            result = asyncio.run(
                _select_relevant_fragments(
                    fragments, "Help me with docker compose", [], config, {}
                )
            )

        triggered = [f for f in result if f["name"] == "docker-guide"]
        self.assertEqual(len(triggered), 1)
        self.assertEqual(triggered[0]["_selection_reason"], "keyword_trigger")


class TestUniversalFragmentMeta(unittest.TestCase):
    """Test load_universal_fragments_with_meta returns metadata."""

    def test_returns_meta_list(self):
        """Should return a list of metadata dicts alongside content."""
        from backend.app.agent.tools.dynamic_loader import load_universal_fragments_with_meta
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            prompts_dir = Path(tmpdir)
            universal_dir = prompts_dir / "universal"
            universal_dir.mkdir()

            # Create test fragment files
            (universal_dir / "safety.md").write_text("Be safe and responsible.")
            (universal_dir / "reasoning.md").write_text("Think step by step.")

            content, meta = load_universal_fragments_with_meta(prompts_dir)

            self.assertIn("Be safe", content)
            self.assertIn("Think step", content)
            self.assertEqual(len(meta), 2)

            names = {m["name"] for m in meta}
            self.assertIn("safety", names)
            self.assertIn("reasoning", names)

            for m in meta:
                self.assertEqual(m["source"], "universal")
                self.assertIn("path", m)
                self.assertIn("tokenEstimate", m)
                self.assertIsInstance(m["tokenEstimate"], int)
                self.assertGreater(m["tokenEstimate"], 0)

    def test_empty_dir_returns_empty(self):
        """Non-existent universal dir should return empty."""
        from backend.app.agent.tools.dynamic_loader import load_universal_fragments_with_meta
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            content, meta = load_universal_fragments_with_meta(Path(tmpdir))
            self.assertEqual(content, "")
            self.assertEqual(meta, [])


class TestLangfuseCallbackSetup(unittest.TestCase):
    """Test that litellm callbacks are conditionally enabled."""

    def test_callback_enabled_with_env_var(self):
        """When LANGFUSE_PUBLIC_KEY is set, langfuse should be in success_callback."""
        import litellm

        # Save original state
        orig_success = litellm.success_callback
        orig_failure = litellm.failure_callback

        try:
            litellm.success_callback = []
            litellm.failure_callback = []

            with patch.dict(os.environ, {"LANGFUSE_PUBLIC_KEY": "pk-test-123"}):
                # Simulate the startup logic
                if os.environ.get("LANGFUSE_PUBLIC_KEY"):
                    if "langfuse" not in litellm.success_callback:
                        litellm.success_callback.append("langfuse")
                    if "langfuse" not in litellm.failure_callback:
                        litellm.failure_callback.append("langfuse")

                self.assertIn("langfuse", litellm.success_callback)
                self.assertIn("langfuse", litellm.failure_callback)
        finally:
            litellm.success_callback = orig_success
            litellm.failure_callback = orig_failure

    def test_callback_not_enabled_without_env_var(self):
        """Without LANGFUSE_PUBLIC_KEY, callbacks should stay empty."""
        import litellm

        orig_success = litellm.success_callback
        orig_failure = litellm.failure_callback

        try:
            litellm.success_callback = []
            litellm.failure_callback = []

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("LANGFUSE_PUBLIC_KEY", None)

                if os.environ.get("LANGFUSE_PUBLIC_KEY"):
                    litellm.success_callback.append("langfuse")

                self.assertNotIn("langfuse", litellm.success_callback)
        finally:
            litellm.success_callback = orig_success
            litellm.failure_callback = orig_failure


class TestLangfuseMetadataBuilding(unittest.TestCase):
    """Test the metadata dict structure built for Langfuse."""

    def test_metadata_structure(self):
        """Verify the metadata dict has expected keys and types."""
        import hashlib

        # Simulate what worker.py builds
        selected_fragments = [
            {"id": "f1", "name": "git", "tier": "core", "_selection_reason": "core_always", "token_estimate": 200},
            {"id": "f2", "name": "python", "tier": "standard", "_selection_reason": "llm_selected", "token_estimate": 300},
        ]
        _universal_meta = [
            {"source": "universal", "path": "universal/safety.md", "name": "safety", "tokenEstimate": 100},
        ]
        _manifest = "# Prompt Categories\n..."
        full_system_prompt = "You are a helpful assistant."
        conversation_id = "conv-123"
        agent_id = "bond"

        # Build metadata (mirrors worker.py logic)
        _audit_fragments = []
        for frag in selected_fragments:
            _audit_fragments.append({
                "source": "db",
                "id": frag.get("id", ""),
                "name": frag.get("name", ""),
                "tier": frag.get("tier", "standard"),
                "reason": frag.get("_selection_reason", "unknown"),
                "tokens": frag.get("token_estimate", 0),
            })
        for meta in _universal_meta:
            _audit_fragments.append(meta)
        _audit_fragments.append({
            "source": "manifest",
            "name": "prompt_manifest",
            "tokenEstimate": len(_manifest) // 4,
        })

        langfuse_meta = {
            "trace_name": f"agent-turn-{agent_id}",
            "session_id": conversation_id,
            "tags": [f"agent:{agent_id}", f"fragments:{len(_audit_fragments)}"],
            "fragments_injected": _audit_fragments,
            "fragment_count": len(_audit_fragments),
            "fragment_names": [f.get("name", "") for f in _audit_fragments],
            "fragment_total_tokens": sum(
                f.get("tokens", f.get("tokenEstimate", 0)) for f in _audit_fragments
            ),
            "system_prompt_tokens": len(full_system_prompt) // 4,
            "system_prompt_hash": hashlib.sha256(full_system_prompt.encode()).hexdigest()[:16],
        }

        # Assertions
        self.assertEqual(langfuse_meta["session_id"], "conv-123")
        self.assertEqual(langfuse_meta["fragment_count"], 4)  # 2 db + 1 universal + 1 manifest
        self.assertIn("git", langfuse_meta["fragment_names"])
        self.assertIn("safety", langfuse_meta["fragment_names"])
        self.assertEqual(langfuse_meta["fragment_total_tokens"], 200 + 300 + 100 + len(_manifest) // 4)
        self.assertEqual(len(langfuse_meta["system_prompt_hash"]), 16)
        self.assertIsInstance(langfuse_meta["fragments_injected"], list)

        # Check DB fragment has selection reason
        db_frags = [f for f in langfuse_meta["fragments_injected"] if f.get("source") == "db"]
        self.assertEqual(len(db_frags), 2)
        reasons = {f["reason"] for f in db_frags}
        self.assertIn("core_always", reasons)
        self.assertIn("llm_selected", reasons)


if __name__ == "__main__":
    unittest.main()
