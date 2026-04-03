# Design Doc 097: Conversation Summarization on Compaction

**Status:** Proposed  
**Date:** 2026-04-02  
**Triggered by:** Comparison of Bond agent loop vs Claude Code source — stability improvements

---

## Problem

When Bond drops old messages to free context space, it loses important information. The model may forget the user's original intent, decisions already made, discovered context (file paths, patterns), and progress state. Currently context_decay.py removes older messages by age/position with no preservation of their information.

Claude Code solves this with multiple summarization strategies: snipCompactIfNeeded, microcompact, reactiveCompact, and cached summaries to avoid re-processing.

---

## Changes

### 1. Define a SummarizationService

**File:** `backend/app/agent/context/summarizer.py` (new file)

```python
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

@dataclass
class SummarizationConfig:
    target_tokens: int = 300
    min_messages_to_summarize: int = 4
    summarization_model: str = "claude-3-haiku-20240307"
    max_input_tokens: int = 4000
    cache_ttl: int = 3600

SUMMARIZATION_PROMPT = """Summarize the following conversation messages into a concise summary.
Focus on preserving:
1. The user's original request and intent
2. Key decisions made and their rationale
3. Important discoveries (file paths, patterns, errors found)
4. What has been completed so far
5. What remains to be done
6. Any approaches that were tried and failed

Be concise but preserve all actionable information. Use bullet points.
Target length: {target_tokens} tokens.

Messages to summarize:
{messages}"""

class SummarizationService:
    def __init__(self, llm_client, cache_manager, config: Optional[SummarizationConfig] = None):
        self._llm = llm_client
        self._cache = cache_manager
        self._config = config or SummarizationConfig()

    async def summarize_messages(self, messages: list[dict], headroom_ratio: float = 1.0) -> str:
        if len(messages) < self._config.min_messages_to_summarize:
            return self._fallback_summary(messages)

        cache_key = self._cache_key(messages)
        cached = await self._cache.get(cache_key)
        if cached:
            logger.debug("summarization_cache_hit", key=cache_key[:16])
            return cached

        target_tokens = self._adjusted_target(headroom_ratio)
        formatted = self._format_messages(messages)
        if len(formatted) > self._config.max_input_tokens * 4:
            formatted = formatted[:self._config.max_input_tokens * 4] + "\n\n[... truncated ...]"

        prompt = SUMMARIZATION_PROMPT.format(target_tokens=target_tokens, messages=formatted)

        try:
            summary = await self._llm.complete(
                model=self._config.summarization_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=target_tokens * 2, temperature=0.0,
            )
            await self._cache.set(cache_key, summary, ttl=self._config.cache_ttl)
            logger.info("messages_summarized", input_messages=len(messages),
                       summary_length=len(summary), headroom_ratio=round(headroom_ratio, 2))
            return summary
        except Exception as e:
            logger.error("summarization_failed", error=str(e), input_messages=len(messages))
            return self._fallback_summary(messages)

    def _adjusted_target(self, headroom_ratio: float) -> int:
        base = self._config.target_tokens
        if headroom_ratio > 0.5:
            return base
        elif headroom_ratio > 0.2:
            return int(base * 0.6)
        else:
            return int(base * 0.3)

    def _fallback_summary(self, messages: list[dict]) -> str:
        parts = ["Summary of earlier conversation:"]
        for msg in messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    parts.append(f"- Original request: {content[:300]}")
                    break
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    parts.append(f"- Last progress: {content[:300]}")
                    break
        tool_calls = sum(1 for m in messages if m.get("role") == "tool")
        if tool_calls:
            parts.append(f"- Tool calls completed: {tool_calls}")
        return "\n".join(parts)

    def _format_messages(self, messages: list[dict]) -> str:
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, str):
                if len(content) > 500:
                    content = content[:500] + "... [truncated]"
                lines.append(f"[{role}]: {content}")
        return "\n\n".join(lines)

    @staticmethod
    def _cache_key(messages: list[dict]) -> str:
        canonical = json.dumps(
            [{"role": m.get("role"), "content": str(m.get("content", ""))[:100]} for m in messages],
            sort_keys=True,
        )
        return f"summary:{hashlib.sha256(canonical.encode()).hexdigest()}"
```

### 2. Integrate with Context Decay

**File:** `backend/app/agent/context/context_decay.py`

```python
from backend.app.agent.context.summarizer import SummarizationService

class ContextDecay:
    def __init__(self, summarizer: SummarizationService, **kwargs):
        self._summarizer = summarizer

    async def decay_messages(self, messages: list[dict], token_budget: int, current_tokens: int) -> list[dict]:
        if current_tokens <= token_budget:
            return messages

        droppable = [m for m in messages if m.get("role") != "system"]
        messages_to_drop = self._select_messages_to_drop(droppable, current_tokens - token_budget)
        if not messages_to_drop:
            return messages

        headroom_ratio = max(0.0, min(1.0, 1.0 - (current_tokens / token_budget) + 0.5))
        summary = await self._summarizer.summarize_messages(messages_to_drop, headroom_ratio=headroom_ratio)

        remaining = [m for m in messages if m not in messages_to_drop]
        summary_msg = {
            "role": "system",
            "content": f"📝 Summary of earlier conversation (compacted to save space):\n\n{summary}",
        }
        system_count = sum(1 for m in remaining if m.get("role") == "system")
        remaining.insert(system_count, summary_msg)

        logger.info("context_decayed_with_summary", messages_dropped=len(messages_to_drop),
                    summary_length=len(summary))
        return remaining
```

### 3. Integrate with Context Pipeline

**File:** `backend/app/agent/context/context_pipeline.py`

```python
class ContextPipeline:
    async def assemble(self, messages: list[dict], config: PipelineConfig) -> list[dict]:
        current_tokens = self._count_tokens(messages)
        if current_tokens > config.token_budget:
            messages = await self._decay.decay_messages(
                messages, token_budget=config.token_budget, current_tokens=current_tokens)
        return messages
```

### 4. Cache Integration

**File:** `backend/app/agent/context/cache_manager.py`

```python
class CacheManager:
    async def get_summary(self, messages_hash: str) -> str | None:
        return await self.get(f"summary:{messages_hash}")

    async def set_summary(self, messages_hash: str, summary: str, ttl: int = 3600) -> None:
        await self.set(f"summary:{messages_hash}", summary, ttl=ttl)
```

---

## Priority & Ordering

| # | Change | Severity | Effort |
|---|--------|----------|--------|
| 1 | SummarizationService | **Foundation** — core summarization logic | 60 min |
| 2 | Context decay integration | **Critical** — summarize before dropping | 30 min |
| 3 | Context pipeline integration | **Required** — wire into assembly | 20 min |
| 4 | Cache integration | **Performance** — avoid re-summarizing | 15 min |

---

## Files Affected

- `backend/app/agent/context/summarizer.py` — new file
- `backend/app/agent/context/context_decay.py` — summarize before dropping
- `backend/app/agent/context/context_pipeline.py` — wire summarization into assembly
- `backend/app/agent/context/cache_manager.py` — cache summaries

---

## Risks

- **Summarization cost** — extra LLM call per compaction. Mitigation: use cheapest model; caching prevents re-summarization.
- **Summarization latency** — 1-3 seconds added. Mitigation: fast model; losing context is worse.
- **Summary quality** — may miss details. Mitigation: specific prompt; fallback extraction; model can re-read files.
- **Summary hallucination** — model could fabricate. Mitigation: temperature=0; labeled as summary.
- **Recursive summarization** — summaries of summaries degrade. Mitigation: mark summary messages for longer retention.

---

## Not Addressed Here

- **Token counting** — see doc 090 (Token-Aware Context Management)
- **Overflow recovery** — see doc 091 (Overflow Recovery)
- **Multi-modal summarization** — future work
- **User-facing summary UI** — future work
