"""API key resolution for LLM providers.

Extracted from worker._run_agent_loop to decouple key resolution logic.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("bond.agent.worker")


class ApiKeyResolver:
    """Resolves API keys and normalizes model strings for litellm."""

    # Headers required for OAuth tokens (sk-ant-oat) to work with Anthropic API
    OAUTH_EXTRA_HEADERS = {
        "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
        "user-agent": "claude-cli/2.1.81",
        "x-app": "cli",
        "anthropic-dangerous-direct-browser-access": "true",
    }

    def __init__(
        self,
        injected_keys: dict[str, str],
        provider_aliases: dict[str, str],
        litellm_prefixes: dict[str, str],
        persistence: Any = None,
    ):
        self.injected_keys = injected_keys
        self.provider_aliases = provider_aliases
        self.litellm_prefixes = litellm_prefixes
        self.persistence = persistence
        self._oauth_extra_headers: dict[str, str] = {}

    def normalize_model_for_litellm(self, model_id: str) -> str:
        """Normalize model string so litellm recognizes the provider prefix.

        If the model uses a provider ID as prefix (e.g., 'google/gemini-2.5-flash'),
        replace it with the litellm prefix (e.g., 'gemini/gemini-2.5-flash').
        """
        if "/" not in model_id or not self.litellm_prefixes:
            return model_id
        prefix, rest = model_id.split("/", 1)
        if prefix in self.litellm_prefixes and self.litellm_prefixes[prefix] != prefix:
            normalized = f"{self.litellm_prefixes[prefix]}/{rest}"
            logger.info("Normalized model %s → %s for litellm", model_id, normalized)
            return normalized
        return model_id

    def resolve_provider(self, model_id: str) -> str:
        """Resolve model prefix to canonical provider ID using DB aliases."""
        if "/" in model_id:
            prefix = model_id.split("/")[0]
            return self.provider_aliases.get(prefix, prefix)

        model_lower = model_id.lower()
        for alias in self.provider_aliases:
            if model_lower.startswith(alias.lower() + "-"):
                return self.provider_aliases.get(alias, alias)

        return "anthropic"

    @staticmethod
    def is_oauth_token(key: str) -> bool:
        """Detect an OAuth token by its prefix."""
        return key.startswith("sk-ant-oat")

    def get_extra_headers(self) -> dict[str, str]:
        """Return extra headers needed for the resolved key (OAuth headers or empty)."""
        return dict(self._oauth_extra_headers)

    async def resolve_api_key(self, model_id: str) -> str | None:
        """Resolve API key: injected from host DB → SpacetimeDB → Vault → env var."""
        prov = self.resolve_provider(model_id)
        logger.error("DEBUG: Resolving API key for provider: %s (model: %s)", prov, model_id)

        # 1. Keys from provider_api_keys (injected at container launch)
        key = self.injected_keys.get(prov)
        if key:
            logger.error("DEBUG: Got API key for %s from injected_keys (length: %d, starts with: %s)",
                        prov, len(key), key[:10] if len(key) > 10 else key)
            return key
        else:
            logger.error("DEBUG: No API key for %s in injected_keys", prov)

        # 2. SpacetimeDB via Gateway (encrypted API keys)
        try:
            if self.persistence and self.persistence.mode == "api":
                logger.error("DEBUG: Trying to get API key for %s from SpacetimeDB (mode: api)", prov)

                # Try provider_api_keys table first
                encrypted_key = await self.persistence.get_provider_api_key(prov)
                if not encrypted_key and prov == "gemini":
                    logger.error("DEBUG: No key found for provider 'gemini', trying 'google' as fallback")
                    encrypted_key = await self.persistence.get_provider_api_key("google")

                if encrypted_key:
                    logger.error("DEBUG: Got encrypted key for %s from provider_api_keys table (encrypted length: %d, starts with: %s)",
                                prov, len(encrypted_key), encrypted_key[:20])
                    from backend.app.core.crypto import decrypt_value
                    decrypted = decrypt_value(encrypted_key)
                    logger.error("DEBUG: Decrypted key for %s (length: %d, starts with: %s, is_encrypted: %s)",
                                prov, len(decrypted), decrypted[:10] if len(decrypted) > 10 else decrypted,
                                encrypted_key.startswith("enc:"))
                    if decrypted and decrypted != encrypted_key:
                        decrypted = decrypted.strip()
                        logger.error("DEBUG: Got API key for %s from SpacetimeDB provider_api_keys (length: %d, starts with: %s)",
                                    prov, len(decrypted), decrypted[:10] if len(decrypted) > 10 else decrypted)
                        return decrypted
                    else:
                        logger.error("DEBUG: Decryption failed or returned same value for %s", prov)

                # Try provider_api_keys table for LLM API keys {provider}
                logger.error("DEBUG: Trying provider_api_keys table with key: %s", prov)
                encrypted_llm_key = await self.persistence.get_provider_api_key(prov)
                if not encrypted_llm_key and prov == "gemini":
                    logger.error("DEBUG: No llm.api_key.gemini setting found, trying google")
                    encrypted_llm_key = await self.persistence.get_provider_api_key("google")

                logger.error("DEBUG: encrypted_llm_key: %s", encrypted_llm_key)

                if encrypted_llm_key:
                    logger.error("DEBUG: Got encrypted key for %s from settings table (encrypted length: %d)", prov, len(encrypted_llm_key))
                    from backend.app.core.crypto import decrypt_value
                    decrypted = decrypt_value(encrypted_llm_key)
                    if decrypted and decrypted != encrypted_llm_key:
                        decrypted = decrypted.strip()
                        logger.error("DEBUG: Got API key for %s from SpacetimeDB settings (llm.api_key) (length: %d)", prov, len(decrypted))
                        return decrypted

                # Try settings table for embedding API keys (embedding.api_key.{provider})
                if prov == "google":
                    embedding_key_name = "embedding.api_key.gemini"
                    logger.debug("Trying embedding API key with key: %s", embedding_key_name)
                    embedding_key = await self.persistence.get_setting(embedding_key_name)
                    if embedding_key:
                        logger.debug("Got encrypted embedding key for google/gemini (encrypted length: %d)", len(embedding_key))
                        from backend.app.core.crypto import decrypt_value
                        decrypted = decrypt_value(embedding_key)
                        if decrypted and decrypted != embedding_key:
                            decrypted = decrypted.strip()
                            logger.debug("Got embedding API key for google/gemini from SpacetimeDB settings (length: %d)", len(decrypted))
                            return decrypted
            else:
                logger.debug("Not trying SpacetimeDB (persistence: %s, mode: %s)",
                            self.persistence, self.persistence.mode if self.persistence else "none")
        except Exception as e:
            logger.debug("Could not read API key from SpacetimeDB for %s: %s", prov, e, exc_info=True)

        # 3. Vault (mounted from host)
        try:
            from backend.app.core.vault import Vault
            vault = Vault()
            key = vault.get_api_key(prov)
            if key:
                return key
        except Exception as e:
            logger.debug("Could not read API key from vault for %s: %s", prov, e)

        # 4. Environment variable
        env_key = os.environ.get(f"{prov.upper()}_API_KEY")
        if env_key:
            return env_key

        # Special case: Google provider can use GEMINI_API_KEY
        if prov == "google":
            key = os.environ.get("GEMINI_API_KEY")
            if key:
                return key

        return None

    async def resolve_all(self, model: str, utility_model_raw: str) -> tuple[str, dict, dict, str]:
        """Resolve primary and utility model keys.

        Returns (normalized_model, extra_kwargs, utility_kwargs, normalized_utility_model).
        """
        normalized_model = self.normalize_model_for_litellm(model)

        extra_kwargs: dict = {}
        primary_key = await self.resolve_api_key(normalized_model)
        if primary_key:
            extra_kwargs["api_key"] = primary_key
            if self.is_oauth_token(primary_key):
                self._oauth_extra_headers = dict(self.OAUTH_EXTRA_HEADERS)
                extra_kwargs["extra_headers"] = dict(self.OAUTH_EXTRA_HEADERS)
                logger.info("Detected OAuth token for primary model — injecting extra headers")

        normalized_utility = self.normalize_model_for_litellm(utility_model_raw)
        utility_kwargs: dict = {}
        utility_key = await self.resolve_api_key(normalized_utility)
        if utility_key:
            utility_kwargs["api_key"] = utility_key
            if self.is_oauth_token(utility_key):
                utility_kwargs["extra_headers"] = dict(self.OAUTH_EXTRA_HEADERS)
                logger.info("Detected OAuth token for utility model — injecting extra headers")

        return normalized_model, extra_kwargs, utility_kwargs, normalized_utility
