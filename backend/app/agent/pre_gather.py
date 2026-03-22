"""Pre-Gather — Plan and Gather phases for agent turns.

Phase 1 (Plan): Single LLM call to analyze the task and list needed files.
Phase 2 (Gather): Direct tool execution to pre-load context.

Design Doc 038.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import litellm

logger = logging.getLogger("bond.agent.pre_gather")

# ── Token budget for gathered context ──
GATHER_TOKEN_BUDGET = 80_000
_ESTIMATE_CHARS_PER_TOKEN = 4

# Short messages that skip planning entirely
_GREETING_PATTERNS = re.compile(
    r"^(hi|hey|hello|thanks|thank you|ok|okay|yes|no|sure|yep|nope|👍|🙏|bye|gm|gn)\s*[!.?]*$",
    re.IGNORECASE,
)
_MIN_PLAN_MESSAGE_LENGTH = 20

# ── Plan system prompt ──

PLAN_SYSTEM_PROMPT = """\
You are about to handle a task. Before taking any action, analyze what you need.

Here is a structural map of the repository showing file paths, class/function \
signatures, and key relationships:
{repo_map}

Output a JSON plan (and ONLY the JSON, no other text):
{{
  "complexity": "simple" | "moderate" | "complex",
  "approach": "brief description of how you'll handle this",
  "files_to_read": [
    "backend/app/worker.py",
    "backend/app/agent/tools/coding_agent.py"
  ],
  "grep_patterns": [
    {{"pattern": "utility_model", "directory": "backend/"}}
  ],
  "delegate_to_coding_agent": false,
  "estimated_iterations": 3
}}

Rules:
- For simple questions (greetings, factual answers), set complexity to "simple" \
and files_to_read to []. You'll answer directly.
- For code tasks, list ALL files you expect to need. The map above shows function \
and class signatures — use them to target the exact files containing the symbols \
you need. Over-estimate — it's cheap to read extra files.
- If this should be delegated to a coding agent, say so upfront. Don't spend \
iterations gathering context only to delegate at the end.
- Keep files_to_read under 15 files. Prefer smaller files when both contain what you need.
- grep_patterns is optional — only include if you need to search for specific patterns.
"""


def _should_skip_plan(user_message: str) -> bool:
    """Return True for messages too short/simple to warrant a plan."""
    stripped = user_message.strip()
    if len(stripped) < _MIN_PLAN_MESSAGE_LENGTH:
        return True
    if _GREETING_PATTERNS.match(stripped):
        return True
    return False


def _extract_json(text: str) -> dict | None:
    """Extract JSON from an LLM response, handling markdown code blocks."""
    if not text:
        return None

    # Try direct parse first
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code block
    patterns = [
        r"```json\s*\n(.*?)\n\s*```",
        r"```\s*\n(.*?)\n\s*```",
        r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}",  # nested braces
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            candidate = match.group(1) if match.lastindex else match.group(0)
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

    return None


def _validate_plan(plan: dict) -> dict | None:
    """Validate and normalize a parsed plan. Returns None if invalid."""
    if not isinstance(plan, dict):
        return None

    # Required fields with defaults
    complexity = plan.get("complexity", "moderate")
    if complexity not in ("simple", "moderate", "complex"):
        complexity = "moderate"

    files_to_read = plan.get("files_to_read", [])
    if not isinstance(files_to_read, list):
        files_to_read = []
    # Sanitize: only strings, strip whitespace
    files_to_read = [f.strip() for f in files_to_read if isinstance(f, str) and f.strip()]
    # Cap at 15 files
    files_to_read = files_to_read[:15]

    grep_patterns = plan.get("grep_patterns", [])
    if not isinstance(grep_patterns, list):
        grep_patterns = []
    # Validate grep entries
    valid_greps = []
    for g in grep_patterns[:5]:
        if isinstance(g, dict) and "pattern" in g:
            valid_greps.append({
                "pattern": str(g["pattern"]),
                "directory": str(g.get("directory", ".")),
            })
    grep_patterns = valid_greps

    return {
        "complexity": complexity,
        "approach": str(plan.get("approach", "")),
        "files_to_read": files_to_read,
        "grep_patterns": grep_patterns,
        "delegate_to_coding_agent": bool(plan.get("delegate_to_coding_agent", False)),
        "estimated_iterations": int(plan.get("estimated_iterations", 5)),
    }


async def plan_phase(
    user_message: str,
    history: list[dict],
    repo_map: str,
    model: str,
    *,
    api_key: str | None = None,
    interrupt_event: asyncio.Event | None = None,
    langfuse_meta: dict | None = None,
    **llm_kwargs: Any,
) -> dict | None:
    """Phase 1: Single LLM call to produce a structured plan.

    Returns a validated plan dict, or None if planning should be skipped
    or if the LLM response can't be parsed.
    """
    if _should_skip_plan(user_message):
        logger.info("Pre-gather: skipping plan for short/simple message")
        return None

    if not repo_map:
        logger.info("Pre-gather: no repo map available, skipping plan")
        return None

    system_content = PLAN_SYSTEM_PROMPT.format(repo_map=repo_map)

    plan_messages = [
        {"role": "system", "content": system_content},
    ]

    # Include recent history for context (last 6 messages max to keep it small)
    if history:
        # Only include user/assistant text messages, skip tool calls/results
        recent = []
        for msg in history[-6:]:
            role = msg.get("role")
            content = msg.get("content", "")
            if role in ("user", "assistant") and isinstance(content, str) and content.strip():
                recent.append({"role": role, "content": content[:500]})  # truncate long messages
        plan_messages.extend(recent)

    plan_messages.append({"role": "user", "content": user_message})

    call_kwargs: dict[str, Any] = {
        "model": model,
        "messages": plan_messages,
        "temperature": 0.3,
        "max_tokens": 2000,
    }
    if api_key:
        call_kwargs["api_key"] = api_key

    # Langfuse metadata for the plan call
    if langfuse_meta:
        plan_meta = dict(langfuse_meta)
        plan_meta["trace_name"] = plan_meta.get("trace_name", "agent-turn") + "-plan"
        plan_meta.setdefault("tags", []).append("phase:plan")
        call_kwargs["metadata"] = plan_meta

    # Merge any extra kwargs (provider-specific settings)
    for k, v in llm_kwargs.items():
        if k not in call_kwargs:
            call_kwargs[k] = v

    # Inject OAuth system prompt prefix if needed
    from backend.app.core.oauth import ensure_oauth_system_prefix
    ensure_oauth_system_prefix(call_kwargs["messages"], extra_kwargs=call_kwargs)

    try:
        if interrupt_event:
            # Use cancellable pattern
            llm_task = asyncio.create_task(litellm.acompletion(**call_kwargs))
            interrupt_task = asyncio.create_task(interrupt_event.wait())
            done, pending = await asyncio.wait(
                {llm_task, interrupt_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            if llm_task not in done:
                logger.info("Plan phase interrupted")
                return None
            response = llm_task.result()
        else:
            response = await litellm.acompletion(**call_kwargs)
    except Exception as e:
        logger.warning("Plan phase LLM call failed: %s", e)
        return None

    # Extract plan from response
    content = response.choices[0].message.content if response.choices else None
    if not content:
        logger.warning("Plan phase: empty LLM response")
        return None

    raw_plan = _extract_json(content)
    if raw_plan is None:
        logger.warning("Plan phase: could not parse JSON from response: %s", content[:200])
        return None

    plan = _validate_plan(raw_plan)
    if plan is None:
        logger.warning("Plan phase: plan validation failed")
        return None

    logger.info(
        "Plan phase: complexity=%s, files=%d, greps=%d, delegate=%s, est_iters=%d",
        plan["complexity"],
        len(plan["files_to_read"]),
        len(plan["grep_patterns"]),
        plan["delegate_to_coding_agent"],
        plan["estimated_iterations"],
    )

    return plan


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: len(text) / 4."""
    return len(text) // _ESTIMATE_CHARS_PER_TOKEN


async def gather_phase(
    plan: dict,
    tool_registry: Any,
    tool_context: dict[str, Any],
    repo_root: str = "/workspace",
) -> str:
    """Phase 2: Execute tool calls from the plan directly (no LLM).

    Reads files and runs greps in parallel, then formats results as a
    markdown context bundle.

    Returns the formatted context string (may be empty if nothing to gather).
    """
    files_to_read = plan.get("files_to_read", [])
    grep_patterns = plan.get("grep_patterns", [])

    if not files_to_read and not grep_patterns:
        return ""

    results: list[str] = []
    token_count = 0

    async def _read_file(path: str) -> tuple[str, str]:
        """Read a file via the native registry."""
        try:
            handler = tool_registry.get("file_read")
            if handler:
                result = await handler({"path": path}, tool_context)
                content = result if isinstance(result, str) else json.dumps(result)
                return path, content
        except Exception as e:
            logger.debug("Gather: failed to read %s: %s", path, e)
        return path, f"[Error reading file: {path}]"

    async def _run_grep(pattern: str, directory: str) -> tuple[str, str]:
        """Run grep via shell."""
        try:
            import subprocess
            cmd = ["grep", "-rn", "--include=*.py", "--include=*.ts", "--include=*.js",
                   "--include=*.md", pattern, directory]
            proc = await asyncio.to_thread(
                subprocess.run, cmd,
                capture_output=True, text=True, cwd=repo_root, timeout=10,
            )
            output = proc.stdout[:10000] if proc.stdout else "(no matches)"
            return f"grep '{pattern}' {directory}", output
        except Exception as e:
            logger.debug("Gather: grep failed for '%s' in %s: %s", pattern, directory, e)
            return f"grep '{pattern}' {directory}", f"[Error: {e}]"

    # Launch all reads and greps in parallel
    tasks = []
    for path in files_to_read:
        tasks.append(_read_file(path))
    for grep in grep_patterns:
        tasks.append(_run_grep(grep["pattern"], grep["directory"]))

    gathered = await asyncio.gather(*tasks, return_exceptions=True)

    for item in gathered:
        if isinstance(item, Exception):
            logger.debug("Gather: task failed with %s", item)
            continue

        label, content = item
        section = f"### {label}\n```\n{content}\n```"
        section_tokens = _estimate_tokens(section)

        # Token budget enforcement
        if token_count + section_tokens > GATHER_TOKEN_BUDGET:
            # Truncate this section to fit
            remaining_budget = GATHER_TOKEN_BUDGET - token_count
            if remaining_budget > 500:  # only include if meaningful
                max_chars = remaining_budget * _ESTIMATE_CHARS_PER_TOKEN
                truncated = content[:max_chars] + "\n... [truncated]"
                section = f"### {label}\n```\n{truncated}\n```"
                results.append(section)
            logger.info("Gather: token budget reached (%d tokens), stopping", token_count)
            break

        results.append(section)
        token_count += section_tokens

    context_bundle = "\n\n".join(results)
    logger.info("Gather: collected %d sections, ~%d tokens", len(results), token_count)
    return context_bundle


async def compress_gathered_context(
    context_bundle: str,
    approach: str,
    utility_model: str,
    utility_kwargs: dict[str, Any],
) -> str:
    """Compress gathered context using the utility model if it's too large.

    Keeps function signatures, key logic, and structure.
    Removes boilerplate, imports, and unrelated code.
    """
    if _estimate_tokens(context_bundle) <= GATHER_TOKEN_BUDGET:
        return context_bundle

    try:
        from backend.app.core.oauth import ensure_oauth_system_prefix
        _compress_msgs = [
            {
                "role": "system",
                "content": (
                    "Compress the following code/content to only the parts relevant "
                    f"to this task: {approach}. Keep function signatures, key logic, "
                    "and structure. Remove boilerplate, imports, and unrelated code. "
                    "Preserve file path headers (### path/to/file)."
                ),
            },
            {"role": "user", "content": context_bundle},
        ]
        ensure_oauth_system_prefix(_compress_msgs, extra_kwargs=utility_kwargs)
        response = await litellm.acompletion(
            model=utility_model,
            messages=_compress_msgs,
            temperature=0.2,
            max_tokens=16000,
            **utility_kwargs,
        )
        compressed = response.choices[0].message.content
        if compressed:
            original_tokens = _estimate_tokens(context_bundle)
            compressed_tokens = _estimate_tokens(compressed)
            logger.info(
                "Compressed gathered context: %d → %d tokens (%.0f%% reduction)",
                original_tokens, compressed_tokens,
                (1 - compressed_tokens / original_tokens) * 100 if original_tokens else 0,
            )
            return compressed
    except Exception as e:
        logger.warning("Context compression failed, using uncompressed: %s", e)

    return context_bundle


def compute_adaptive_budget(plan: dict, max_iterations: int) -> int | None:
    """Compute adaptive iteration budget from the plan.

    Returns None if no plan-based budget should be set.
    """
    complexity = plan.get("complexity", "moderate")
    delegate = plan.get("delegate_to_coding_agent", False)

    if delegate:
        return min(max_iterations, 5)
    elif complexity == "simple":
        return min(max_iterations, 3)
    elif complexity == "moderate":
        return min(max_iterations, 12)
    elif complexity == "complex":
        return min(max_iterations, 20)

    return None


def build_handoff_context(messages: list[dict]) -> dict:
    """Extract what the agent has done so far from the message history.

    Scans tool calls and results to build:
    - List of files read (with paths)
    - List of edits made (file paths)

    Used for structured handoff to coding_agent at budget escalation.
    """
    files_read: list[str] = []
    edits_made: list[str] = []

    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []):
            if isinstance(tc, dict):
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    args = {}
            else:
                # litellm ChatCompletionMessage object
                fn = getattr(tc, "function", None)
                if fn is None:
                    continue
                name = getattr(fn, "name", "")
                try:
                    args = json.loads(getattr(fn, "arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    args = {}

            if name == "file_read":
                path = args.get("path", "unknown")
                if path not in files_read:
                    files_read.append(path)
            elif name in ("file_edit", "file_write"):
                path = args.get("path", "unknown")
                if path not in edits_made:
                    edits_made.append(path)

    return {
        "files_read": "\n".join(f"- {f}" for f in files_read) or "None",
        "edits_made": "\n".join(f"- {f}" for f in edits_made) or "None",
    }
