"""LLM client — LiteLLM wrapper with provider config from vault."""

from __future__ import annotations

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


def _inject_api_key(provider: str) -> None:
    """Set the API key env var from vault for the given provider."""
    vault = Vault()
    key = vault.get_api_key(provider)
    if key:
        env_var = f"{provider.upper()}_API_KEY"
        os.environ[env_var] = key


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

    _inject_api_key(provider)
    model_string = _resolve_model_string(provider, model)

    logger.info("LLM call: provider=%s model=%s messages=%d", provider, model, len(messages))

    if stream:
        response = await litellm.acompletion(
            model=model_string,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        return _stream_response(response)
    else:
        response = await litellm.acompletion(
            model=model_string,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content


async def _stream_response(response) -> AsyncIterator[str]:
    """Yield text chunks from a streaming LLM response."""
    async for chunk in response:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
