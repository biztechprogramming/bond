"""Tests for model string normalization — litellm provider prefix mapping.

Verifies that model strings using provider IDs (e.g., 'google/gemini-2.5-flash')
are normalized to litellm-compatible prefixes (e.g., 'gemini/gemini-2.5-flash')
before being passed to litellm.acompletion().
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Unit under test: _normalize_model_for_litellm
#
# This function lives inside the worker's async run() closure, capturing
# litellm_prefixes from the config. We replicate its logic here so we can
# test it in isolation without spinning up the full worker.
# ---------------------------------------------------------------------------

def _make_normalizer(litellm_prefixes: dict[str, str]):
    """Build a normalizer function with the given prefix mapping."""
    def _normalize_model_for_litellm(model_id: str) -> str:
        if "/" not in model_id or not litellm_prefixes:
            return model_id
        prefix, rest = model_id.split("/", 1)
        if prefix in litellm_prefixes and litellm_prefixes[prefix] != prefix:
            return f"{litellm_prefixes[prefix]}/{rest}"
        return model_id
    return _normalize_model_for_litellm


# Standard provider mapping from seed_providers.py
STANDARD_PREFIXES = {
    "google": "gemini",
    "anthropic": "anthropic",
    "openai": "openai",
    "deepseek": "deepseek",
    "groq": "groq",
    "mistral": "mistral",
    "xai": "xai",
    "openrouter": "openrouter",
}


class TestNormalizeModelForLitellm:
    """Verify model normalization with standard provider prefix mapping."""

    def setup_method(self):
        self.normalize = _make_normalizer(STANDARD_PREFIXES)

    # -- Google/Gemini (the problematic case) --

    def test_google_prefix_normalized_to_gemini(self):
        assert self.normalize("google/gemini-flash-latest") == "gemini/gemini-flash-latest"

    def test_google_gemini_2_5_flash(self):
        assert self.normalize("google/gemini-2.5-flash") == "gemini/gemini-2.5-flash"

    def test_google_gemini_flash_lite(self):
        assert self.normalize("google/gemini-flash-lite-latest") == "gemini/gemini-flash-lite-latest"

    def test_google_gemini_pro(self):
        assert self.normalize("google/gemini-pro") == "gemini/gemini-pro"

    # -- Already correct prefixes (no-op) --

    def test_gemini_prefix_unchanged(self):
        assert self.normalize("gemini/gemini-flash-latest") == "gemini/gemini-flash-latest"

    def test_anthropic_prefix_unchanged(self):
        assert self.normalize("anthropic/claude-sonnet-4-20250514") == "anthropic/claude-sonnet-4-20250514"

    def test_openai_prefix_unchanged(self):
        assert self.normalize("openai/gpt-4o") == "openai/gpt-4o"

    def test_deepseek_prefix_unchanged(self):
        assert self.normalize("deepseek/deepseek-chat") == "deepseek/deepseek-chat"

    # -- No prefix (bare model names) --

    def test_bare_model_name_unchanged(self):
        assert self.normalize("claude-sonnet-4-20250514") == "claude-sonnet-4-20250514"

    def test_bare_gemini_model_unchanged(self):
        assert self.normalize("gemini-flash-latest") == "gemini-flash-latest"

    # -- Empty prefix mapping --

    def test_empty_prefixes_returns_unchanged(self):
        normalize = _make_normalizer({})
        assert normalize("google/gemini-flash-latest") == "google/gemini-flash-latest"


class TestNormalizationWithLitellm:
    """Integration test: verify normalized models are accepted by litellm's provider logic."""

    def test_google_prefix_fails_in_litellm(self):
        """Prove that google/ prefix is rejected by litellm."""
        try:
            from litellm.litellm_core_utils.get_llm_provider_logic import get_llm_provider
        except ImportError:
            pytest.skip("litellm not installed")

        with pytest.raises(Exception, match="LLM Provider NOT provided"):
            get_llm_provider("google/gemini-flash-latest")

    def test_gemini_prefix_accepted_by_litellm(self):
        """Prove that gemini/ prefix is accepted by litellm."""
        try:
            from litellm.litellm_core_utils.get_llm_provider_logic import get_llm_provider
        except ImportError:
            pytest.skip("litellm not installed")

        model, provider, _, _ = get_llm_provider("gemini/gemini-flash-latest")
        assert provider == "gemini"

    def test_normalized_google_model_accepted(self):
        """The full flow: normalize google/ → gemini/, then litellm accepts it."""
        try:
            from litellm.litellm_core_utils.get_llm_provider_logic import get_llm_provider
        except ImportError:
            pytest.skip("litellm not installed")

        normalize = _make_normalizer(STANDARD_PREFIXES)
        normalized = normalize("google/gemini-flash-latest")
        assert normalized == "gemini/gemini-flash-latest"

        model, provider, _, _ = get_llm_provider(normalized)
        assert provider == "gemini"
