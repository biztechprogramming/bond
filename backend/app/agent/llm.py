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
from backend.app.core.oauth import get_oauth_extra_headers, get_provider_api_key
from backend.app.core.vault import Vault

logger = logging.getLogger("bond.agent.llm")


# ---------------------------------------------------------------------------
# Overflow error types (Doc 091: Overflow Recovery)
# ---------------------------------------------------------------------------

class ContextOverflowError(Exception):
    """Raised when the LLM API rejects the request due to context length."""
    def __init__(self, message: str, tokens_sent: int = 0):
        super().__init__(message)
        self.tokens_sent = tokens_sent


class OutputTruncatedError(Exception):
    """Raised when the model's response was cut off by max_output_tokens."""
    pass


# ---------------------------------------------------------------------------
# Model context window limits (Doc 090: Token-Aware Context Management)
# ---------------------------------------------------------------------------

MODEL_CONTEXT_LIMITS: dict[str, int] = {
    "claude-sonnet-4-20250514": 200_000,
    "claude-3-5-sonnet-20241022": 200_000,
    "claude-3-haiku-20240307": 200_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "deepseek-chat": 64_000,
}

COMPACTION_THRESHOLD = 0.80  # 80% — trigger proactive compaction
HARD_CEILING = 0.95  # 95% — never send more than this


def get_context_limit(model: str) -> int:
    """Return the context window size for a model, defaulting conservatively."""
    for key, limit in MODEL_CONTEXT_LIMITS.items():
        if key in model:
            return limit
    return 64_000  # conservative default for unknown models


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

    # Resolve API key through the Gateway (handles OAuth token refresh)
    # Falls back to legacy _resolve_api_key if Gateway is unreachable
    gateway_result = await get_provider_api_key(provider)
    if gateway_result:
        api_key, key_type = gateway_result
        logger.info("LLM key resolved via Gateway for %s (key_type=%s)", provider, key_type)
    else:
        api_key = await _resolve_api_key(provider)
        logger.info("LLM key resolved via legacy fallback for %s", provider)

    model_string = _resolve_model_string(provider, model)

    logger.info("LLM call: provider=%s model=%s messages=%d", provider, model, len(messages))

    extra_kwargs: dict = {}
    if api_key:
        extra_kwargs["api_key"] = api_key
        oauth_headers = get_oauth_extra_headers(api_key)
        if oauth_headers:
            extra_kwargs["extra_headers"] = oauth_headers

    # Inject OAuth system prompt prefix if needed (centralized)
    from backend.app.core.oauth import ensure_oauth_system_prefix
    ensure_oauth_system_prefix(messages, extra_kwargs=extra_kwargs)

    # Debug: log the exact payload being sent to LiteLLM
    _has_oauth_headers = "extra_headers" in extra_kwargs
    _sys_preview = ""
    if messages and messages[0].get("role") == "system":
        _content = messages[0].get("content", "")
        _sys_preview = _content[:200] if isinstance(_content, str) else str(_content)[:200]
    logger.info(
        "LLM payload: model=%s, oauth_headers=%s, num_messages=%d, sys_preview=%s",
        model_string, _has_oauth_headers, len(messages), _sys_preview,
    )

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
        # Doc 090: estimate input tokens for logging
        from backend.app.agent.context_pipeline import _estimate_messages_tokens
        input_tokens = _estimate_messages_tokens(messages)

        response = await litellm.acompletion(
            model=model_string,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **extra_kwargs,
        )

        # Doc 090: log token counts
        output_tokens = 0
        if hasattr(response, "usage") and response.usage:
            output_tokens = getattr(response.usage, "completion_tokens", 0) or 0
        logger.info(
            "llm_call_complete model=%s input_tokens=%d output_tokens=%d total_tokens=%d",
            model_string, input_tokens, output_tokens, input_tokens + output_tokens,
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


# ---------------------------------------------------------------------------
# Overflow detection (Doc 091: Overflow Recovery)
# ---------------------------------------------------------------------------

_OVERFLOW_PATTERNS = [
    "context_length_exceeded",
    "maximum context length",
    "request too large",
    "413",
    "token limit",
    "too many tokens",
]


def classify_overflow_error(exc: Exception) -> ContextOverflowError | None:
    """If *exc* looks like a context-overflow error, wrap it; else return None."""
    error_str = str(exc).lower()
    if any(term in error_str for term in _OVERFLOW_PATTERNS):
        return ContextOverflowError(str(exc))
    return None
