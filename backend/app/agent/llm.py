"""LLM client — LiteLLM wrapper with provider config from vault."""

from __future__ import annotations
import vendor  # noqa: F401
import instructor

import logging
import os
from pathlib import Path
from typing import AsyncIterator

import litellm
import yaml

from backend.app.config import get_settings
from backend.app.core.vault import Vault

logger = logging.getLogger("bond.agent.llm")

# Load provider config
_PROVIDERS_PATH = Path(__file__).parent / "providers.yaml"


def load_providers() -> dict:
    """Load the model_providers.yaml configuration."""
    with open(_PROVIDERS_PATH) as f:
        return yaml.safe_load(f)


def _resolve_model_string(provider: str, model: str) -> str:
    """Build the LiteLLM model string from provider + model name.

    LiteLLM expects format: ``provider/model`` for most providers.
    """
    providers = load_providers()
    chat_providers = providers.get("chat", {})

    provider_config = chat_providers.get(provider, {})
    litellm_provider = provider_config.get("litellm_provider", provider)

    # Some providers need custom api_base
    if "kwargs" in provider_config:
        kwargs = provider_config["kwargs"]
        if "api_base" in kwargs:
            litellm.api_base = kwargs["api_base"]

    if litellm_provider == provider:
        return f"{litellm_provider}/{model}"

    return f"{litellm_provider}/{model}"


async def _get_api_key_from_settings(provider: str) -> str | None:
    """Read and decrypt an LLM API key from the settings table."""
    from backend.app.db.session import get_session_factory
    from backend.app.core.crypto import decrypt_value

    setting_key = f"llm.api_key.{provider}"
    try:
        factory = get_session_factory()
        async with factory() as session:
            from sqlalchemy import text

            result = await session.execute(
                text("SELECT value FROM settings WHERE key = :key"),
                {"key": setting_key},
            )
            row = result.fetchone()
            if row and row[0]:
                return decrypt_value(row[0])
    except Exception:
        logger.debug("Could not read API key from settings for %s", provider)
    return None


def _inject_api_key(provider: str) -> str | None:
    """Get API key from env var, returning it without mutating os.environ."""
    env_var = f"{provider.upper()}_API_KEY"
    env_key = os.environ.get(env_var)
    if env_key:
        return env_key
    # Try vault file
    vault = Vault()
    return vault.get(f"{provider.upper()}_API_KEY")


async def _resolve_api_key(provider: str) -> str | None:
    """Resolve API key with priority: env var > settings DB > vault file."""
    # 1. Environment variable
    env_var = f"{provider.upper()}_API_KEY"
    env_key = os.environ.get(env_var)
    if env_key:
        return env_key

    # 2. Settings DB (encrypted)
    db_key = await _get_api_key_from_settings(provider)
    if db_key:
        return db_key

    # 3. Vault file
    vault = Vault()
    return vault.get(f"{provider.upper()}_API_KEY")


async def chat_completion(
    messages: list[dict[str, str]],
    *,
    provider: str | None = None,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    stream: bool = False,
) -> str | AsyncIterator[str]:
    """Call an LLM via LiteLLM.

    Returns the full response text (non-streaming) or an async iterator (streaming).
    """
    settings = get_settings()
    provider = provider or settings.llm_provider
    model = model or settings.llm_model

    api_key = await _resolve_api_key(provider)
    model_string = _resolve_model_string(provider, model)

    logger.info("LLM call: provider=%s model=%s messages=%d", provider, model, len(messages))

    extra_kwargs: dict = {}
    if api_key:
        extra_kwargs["api_key"] = api_key

    if stream:
        response = await litellm.acompletion(
            model=model_string,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            **extra_kwargs,
        )
        return _stream_response(response)
    else:
        response = await litellm.acompletion(
            model=model_string,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **extra_kwargs,
        )
        return response.choices[0].message.content


async def _stream_response(response) -> AsyncIterator[str]:
    """Yield text chunks from a streaming LLM response."""
    async for chunk in response:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content

def get_instructor_client():
    """Return a litellm client patched with instructor."""
    return instructor.from_litellm(litellm.acompletion)
