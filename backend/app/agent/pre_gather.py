"""Pre-Gather — Plan and Gather phases for agent turns.

Phase 1 (Plan): Single LLM call to analyze the task and list needed files.
Phase 2 (Gather): Direct tool execution to pre-load context.

Design Docs 038, 068 (Parallel Pre-Gathering).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import litellm

logger = logging.getLogger("bond.agent.pre_gather")

# ── Token budget for gathered context ──
GATHER_TOKEN_BUDGET = 80_000
_ESTIMATE_CHARS_PER_TOKEN = 4

# ── Parallel gathering configuration ──
PARALLEL_GATHERING_ENABLED = os.getenv("PARALLEL_GATHERING_ENABLED", "true").lower() == "true"
PARALLEL_MAX_TASKS = int(os.getenv("PARALLEL_MAX_TASKS", "8"))
PARALLEL_TASK_TIMEOUT = float(os.getenv("PARALLEL_TASK_TIMEOUT", "10.0"))
PARALLEL_WEB_TIMEOUT = float(os.getenv("PARALLEL_WEB_TIMEOUT", "15.0"))

# Lazy-initialized module-level thread pool
_gather_pool: ThreadPoolExecutor | None = None


def _get_gather_pool() -> ThreadPoolExecutor:
    global _gather_pool
    if _gather_pool is None:
        _gather_pool = ThreadPoolExecutor(max_workers=PARALLEL_MAX_TASKS)
    return _gather_pool


# Per-domain concurrency limits for web fetches (limit=1 to serialize same-domain)
_domain_semaphores: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(1))

# Max response body size for web fetches
_WEB_FETCH_MAX_BYTES = 50 * 1024  # 50KB

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html_tags(html: str) -> str:
    """Strip HTML tags for cleaner text content."""
    text = _HTML_TAG_RE.sub("", html)
    # Collapse whitespace runs
    text = re.sub(r"\s+", " ", text).strip()
    return text


@dataclass
class GatherTask:
    name: str
    task_type: str  # "file_read", "grep", "web_fetch", etc.
    params: dict = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    priority: int = 0  # higher = more important


@dataclass
class GatherResult:
    task_name: str
    content: str
    tokens: int
    error: bool = False
    elapsed_ms: float = 0.0


@dataclass
class GatherMetrics:
    total_tasks: int
    parallel_tasks: int
    sequential_tasks: int
    wall_clock_ms: float
    sequential_equivalent_ms: float
    speedup: float
    tasks_timed_out: int
    tasks_failed: int


def partition_by_dependencies(
    tasks: list[GatherTask],
) -> tuple[list[GatherTask], list[GatherTask]]:
    """Split tasks into (independent, dependent) based on depends_on."""
    independent = []
    dependent = []
    for task in tasks:
        if task.depends_on:
            dependent.append(task)
        else:
            independent.append(task)
    return independent, dependent

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

# ── Multi-repo workspace plan prompt (Design Doc 069) ──

WORKSPACE_PLAN_SYSTEM_PROMPT = """\
You are about to handle a task. Before taking any action, analyze what you need.

This workspace contains multiple repositories. Here is a structural overview:
{workspace_overview}

Output a JSON plan (and ONLY the JSON, no other text):
{{
  "complexity": "simple" | "moderate" | "complex",
  "approach": "brief description of how you'll handle this",
  "repos_to_map": ["repo-name"],
  "files_to_read": [
    "repo-name/src/main.py"
  ],
  "grep_patterns": [
    {{"pattern": "some_function", "directory": "repo-name/src/"}}
  ],
  "delegate_to_coding_agent": false,
  "estimated_iterations": 3
}}

Rules:
- For simple questions (greetings, factual answers), set complexity to "simple", \
repos_to_map to [], and files_to_read to []. You'll answer directly.
- repos_to_map: list repo subdirectory names that need a detailed structural map. \
Max 3 repos. Usually 1 is enough — pick the one most relevant to the task.
- files_to_read: use repo-prefixed paths (e.g., "bond/backend/app/worker.py"). \
You can list files from the overview even without a deep map.
- grep_patterns: use repo-prefixed directories. Optional.
- If this should be delegated to a coding agent, say so upfront.
- Keep files_to_read under 15 files.
"""

# ── Second plan prompt: file selection after deep map (Design Doc 069) ──

DEEP_MAP_FILE_SELECT_PROMPT = """\
You are selecting files to pre-read for a coding task.

Task approach: {approach}

Here is the detailed structural map for the relevant repositories:
{deep_map}

Based on the original workspace overview, you initially planned to read these files:
{initial_files}

Now that you have detailed function/class signatures, refine your file selection.
Output ONLY a JSON object:
{{
  "files_to_read": [
    "repo-name/path/to/file.py"
  ],
  "grep_patterns": [
    {{"pattern": "some_function", "directory": "repo-name/src/"}}
  ]
}}

Rules:
- Use repo-prefixed paths (e.g., "bond/backend/app/worker.py").
- Keep files_to_read under 15 files. Target the exact files with symbols you need.
- grep_patterns is optional.
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

    # repos_to_map (optional, for multi-repo workspaces)
    repos_to_map = plan.get("repos_to_map", [])
    if not isinstance(repos_to_map, list):
        repos_to_map = []
    repos_to_map = [r.strip() for r in repos_to_map if isinstance(r, str) and r.strip()]
    # Cap at 3 repos per design doc 069
    repos_to_map = repos_to_map[:3]

    return {
        "complexity": complexity,
        "approach": str(plan.get("approach", "")),
        "repos_to_map": repos_to_map,
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


async def workspace_plan_phase(
    user_message: str,
    history: list[dict],
    workspace_overview: str,
    model: str,
    *,
    api_key: str | None = None,
    interrupt_event: asyncio.Event | None = None,
    langfuse_meta: dict | None = None,
    **llm_kwargs: Any,
) -> dict | None:
    """Phase 1 for multi-repo workspaces: plan with workspace overview.

    Similar to plan_phase but uses WORKSPACE_PLAN_SYSTEM_PROMPT and includes
    repos_to_map in the output schema. Design Doc 069.
    """
    if _should_skip_plan(user_message):
        logger.info("Pre-gather: skipping workspace plan for short/simple message")
        return None

    if not workspace_overview:
        logger.info("Pre-gather: no workspace overview available, skipping plan")
        return None

    system_content = WORKSPACE_PLAN_SYSTEM_PROMPT.format(workspace_overview=workspace_overview)

    plan_messages = [
        {"role": "system", "content": system_content},
    ]

    if history:
        recent = []
        for msg in history[-6:]:
            role = msg.get("role")
            content = msg.get("content", "")
            if role in ("user", "assistant") and isinstance(content, str) and content.strip():
                recent.append({"role": role, "content": content[:500]})
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

    if langfuse_meta:
        plan_meta = dict(langfuse_meta)
        plan_meta["trace_name"] = plan_meta.get("trace_name", "agent-turn") + "-workspace-plan"
        plan_meta.setdefault("tags", []).append("phase:workspace-plan")
        call_kwargs["metadata"] = plan_meta

    for k, v in llm_kwargs.items():
        if k not in call_kwargs:
            call_kwargs[k] = v

    from backend.app.core.oauth import ensure_oauth_system_prefix
    ensure_oauth_system_prefix(call_kwargs["messages"], extra_kwargs=call_kwargs)

    try:
        if interrupt_event:
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
                logger.info("Workspace plan phase interrupted")
                return None
            response = llm_task.result()
        else:
            response = await litellm.acompletion(**call_kwargs)
    except Exception as e:
        logger.warning("Workspace plan phase LLM call failed: %s", e)
        return None

    content = response.choices[0].message.content if response.choices else None
    if not content:
        logger.warning("Workspace plan phase: empty LLM response")
        return None

    raw_plan = _extract_json(content)
    if raw_plan is None:
        logger.warning("Workspace plan phase: could not parse JSON: %s", content[:200])
        return None

    plan = _validate_plan(raw_plan)
    if plan is None:
        logger.warning("Workspace plan phase: plan validation failed")
        return None

    logger.info(
        "Workspace plan phase: complexity=%s, repos_to_map=%s, files=%d, delegate=%s",
        plan["complexity"],
        plan.get("repos_to_map", []),
        len(plan["files_to_read"]),
        plan["delegate_to_coding_agent"],
    )

    return plan


async def deep_map_file_select(
    approach: str,
    deep_map: str,
    initial_files: list[str],
    model: str,
    *,
    api_key: str | None = None,
    langfuse_meta: dict | None = None,
    **llm_kwargs: Any,
) -> tuple[list[str], list[dict]]:
    """Phase 1b: refine file selection using the deep repo map.

    After generating detailed tree-sitter maps for selected repos,
    this makes a lightweight LLM call to refine file and grep selections.

    Returns (files_to_read, grep_patterns).
    """
    if not deep_map:
        return initial_files, []

    system_content = DEEP_MAP_FILE_SELECT_PROMPT.format(
        approach=approach,
        deep_map=deep_map,
        initial_files=json.dumps(initial_files),
    )

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": "Select the files to pre-read."},
    ]

    call_kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 1500,
    }
    if api_key:
        call_kwargs["api_key"] = api_key

    if langfuse_meta:
        select_meta = dict(langfuse_meta)
        select_meta["trace_name"] = select_meta.get("trace_name", "agent-turn") + "-file-select"
        select_meta.setdefault("tags", []).append("phase:file-select")
        call_kwargs["metadata"] = select_meta

    for k, v in llm_kwargs.items():
        if k not in call_kwargs:
            call_kwargs[k] = v

    from backend.app.core.oauth import ensure_oauth_system_prefix
    ensure_oauth_system_prefix(messages, extra_kwargs=call_kwargs)

    try:
        response = await litellm.acompletion(**call_kwargs)
    except Exception as e:
        logger.warning("Deep map file select failed: %s", e)
        return initial_files, []

    content = response.choices[0].message.content if response.choices else None
    if not content:
        return initial_files, []

    parsed = _extract_json(content)
    if not parsed or not isinstance(parsed, dict):
        return initial_files, []

    files = parsed.get("files_to_read", initial_files)
    if not isinstance(files, list):
        files = initial_files
    files = [f.strip() for f in files if isinstance(f, str) and f.strip()][:15]

    greps = parsed.get("grep_patterns", [])
    if not isinstance(greps, list):
        greps = []
    valid_greps = []
    for g in greps[:5]:
        if isinstance(g, dict) and "pattern" in g:
            valid_greps.append({
                "pattern": str(g["pattern"]),
                "directory": str(g.get("directory", ".")),
            })

    logger.info("Deep map file select: %d files, %d greps", len(files), len(valid_greps))
    return files, valid_greps


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: len(text) / 4."""
    return len(text) // _ESTIMATE_CHARS_PER_TOKEN


async def _execute_single_task(
    task: GatherTask,
    tool_registry: Any,
    tool_context: dict[str, Any],
    repo_root: str,
) -> GatherResult:
    """Execute a single GatherTask and return a GatherResult."""
    start = time.monotonic()
    try:
        if task.task_type == "file_read":
            path = task.params.get("path", "")
            handler = tool_registry.get("file_read")
            if handler:
                result = await handler({"path": path}, tool_context)
                content = result if isinstance(result, str) else json.dumps(result)
            else:
                content = f"[Error reading file: {path}]"
        elif task.task_type == "grep":
            import subprocess
            pattern = task.params.get("pattern", "")
            directory = task.params.get("directory", ".")
            cmd = [
                "grep", "-rn", "--include=*.py", "--include=*.ts",
                "--include=*.js", "--include=*.md", pattern, directory,
            ]
            proc = await asyncio.to_thread(
                subprocess.run, cmd,
                capture_output=True, text=True, cwd=repo_root, timeout=10,
            )
            content = proc.stdout[:10000] if proc.stdout else "(no matches)"
        elif task.task_type == "web_fetch":
            import httpx
            url = task.params.get("url", "")
            async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
                resp = await client.get(url, headers={"User-Agent": "Bond-Agent/1.0"})
                resp.raise_for_status()
                body = resp.text[:_WEB_FETCH_MAX_BYTES]
                content_type = resp.headers.get("content-type", "")
                if "html" in content_type:
                    body = _strip_html_tags(body)
                content = body
        else:
            content = f"[Unsupported task type: {task.task_type}]"
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        logger.debug("Gather: task %s failed: %s", task.name, e)
        return GatherResult(
            task_name=task.name, content=f"[Error: {e}]",
            tokens=0, error=True, elapsed_ms=elapsed,
        )

    elapsed = (time.monotonic() - start) * 1000
    tokens = _estimate_tokens(content)
    return GatherResult(
        task_name=task.name, content=content,
        tokens=tokens, error=False, elapsed_ms=elapsed,
    )


async def _execute_with_timeout(
    task: GatherTask,
    tool_registry: Any,
    tool_context: dict[str, Any],
    repo_root: str,
) -> GatherResult:
    """Execute a task with a timeout. Returns GatherResult with error=True on timeout."""
    timeout = PARALLEL_WEB_TIMEOUT if task.task_type == "web_fetch" else PARALLEL_TASK_TIMEOUT
    try:
        return await asyncio.wait_for(
            _execute_single_task(task, tool_registry, tool_context, repo_root),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("Gather: task %s timed out after %.1fs", task.name, timeout)
        return GatherResult(
            task_name=task.name, content=f"[Timeout after {timeout}s]",
            tokens=0, error=True, elapsed_ms=timeout * 1000,
        )


async def _execute_with_domain_limit(
    task: GatherTask,
    tool_registry: Any,
    tool_context: dict[str, Any],
    repo_root: str,
) -> GatherResult:
    """Wrap execution with per-domain concurrency limiting for web fetches."""
    if task.task_type == "web_fetch":
        url = task.params.get("url", "")
        domain = urlparse(url).netloc
        async with _domain_semaphores[domain]:
            return await _execute_with_timeout(task, tool_registry, tool_context, repo_root)
    return await _execute_with_timeout(task, tool_registry, tool_context, repo_root)


async def _execute_dependent_tasks(
    dependent_tasks: list[GatherTask],
    result_map: dict[str, GatherResult],
    tool_registry: Any,
    tool_context: dict[str, Any],
    repo_root: str,
    cancellation_event: asyncio.Event | None = None,
) -> list[GatherResult]:
    """Execute dependent tasks in tiers via topological sort."""
    remaining = list(dependent_tasks)
    results: list[GatherResult] = []

    while remaining:
        if cancellation_event and cancellation_event.is_set():
            logger.info("Gather: cancellation requested, skipping %d dependent tasks", len(remaining))
            for t in remaining:
                results.append(GatherResult(
                    task_name=t.name, content="[Cancelled]",
                    tokens=0, error=True, elapsed_ms=0.0,
                ))
            break

        ready = []
        skip = []
        still_waiting = []

        for task in remaining:
            # Check if any dependency failed
            failed_deps = [d for d in task.depends_on if d in result_map and result_map[d].error]
            if failed_deps:
                logger.warning("Gather: skipping %s — dependency %s failed", task.name, failed_deps)
                skip.append(task)
                continue

            # Check if all dependencies are resolved
            if all(d in result_map for d in task.depends_on):
                ready.append(task)
            else:
                still_waiting.append(task)

        # Skip failed-dep tasks
        for task in skip:
            r = GatherResult(
                task_name=task.name, content="[Skipped: dependency failed]",
                tokens=0, error=True, elapsed_ms=0.0,
            )
            results.append(r)
            result_map[task.name] = r

        if not ready:
            if still_waiting:
                # Circular dependency
                logger.warning("Gather: circular dependency detected, skipping %d tasks", len(still_waiting))
                for task in still_waiting:
                    r = GatherResult(
                        task_name=task.name, content="[Skipped: circular dependency]",
                        tokens=0, error=True, elapsed_ms=0.0,
                    )
                    results.append(r)
                    result_map[task.name] = r
            break

        # Execute ready tier in parallel
        tier_results = await asyncio.gather(*[
            _execute_with_timeout(t, tool_registry, tool_context, repo_root)
            for t in ready
        ])
        for r in tier_results:
            results.append(r)
            result_map[r.task_name] = r

        remaining = still_waiting

    return results


def _plan_to_tasks(plan: dict) -> list[GatherTask]:
    """Convert a plan dict into a list of GatherTask objects."""
    tasks = []
    for i, path in enumerate(plan.get("files_to_read", [])):
        tasks.append(GatherTask(
            name=f"file:{path}",
            task_type="file_read",
            params={"path": path},
            priority=len(plan.get("files_to_read", [])) - i,
        ))
    for i, grep in enumerate(plan.get("grep_patterns", [])):
        tasks.append(GatherTask(
            name=f"grep:{grep['pattern']}:{grep['directory']}",
            task_type="grep",
            params={"pattern": grep["pattern"], "directory": grep["directory"]},
            priority=0,
        ))
    for i, url in enumerate(plan.get("urls_to_fetch", [])):
        tasks.append(GatherTask(
            name=f"web:{url}",
            task_type="web_fetch",
            params={"url": url},
            priority=0,
        ))
    return tasks


def _format_results(
    gather_results: list[GatherResult],
    original_tasks: list[GatherTask],
) -> str:
    """Format GatherResults into the markdown context bundle, respecting token budget."""
    # Sort by original priority (higher first)
    task_priority = {t.name: t.priority for t in original_tasks}
    sorted_results = sorted(
        gather_results,
        key=lambda r: task_priority.get(r.task_name, 0),
        reverse=True,
    )

    sections: list[str] = []
    token_count = 0

    for r in sorted_results:
        if r.error:
            continue
        section = f"### {r.task_name}\n```\n{r.content}\n```"
        section_tokens = _estimate_tokens(section)

        if token_count + section_tokens > GATHER_TOKEN_BUDGET:
            remaining_budget = GATHER_TOKEN_BUDGET - token_count
            if remaining_budget > 500:
                max_chars = remaining_budget * _ESTIMATE_CHARS_PER_TOKEN
                truncated = r.content[:max_chars] + "\n... [truncated]"
                section = f"### {r.task_name}\n```\n{truncated}\n```"
                sections.append(section)
            logger.info("Gather: token budget reached (%d tokens), stopping", token_count)
            break

        sections.append(section)
        token_count += section_tokens

    return "\n\n".join(sections)


async def gather_phase(
    plan: dict,
    tool_registry: Any,
    tool_context: dict[str, Any],
    repo_root: str = "/workspace",
    cancellation_event: asyncio.Event | None = None,
) -> tuple[str, GatherMetrics | None]:
    """Phase 2: Execute tool calls from the plan directly (no LLM).

    Reads files and runs greps in parallel, then formats results as a
    markdown context bundle.

    Returns (formatted_context_string, metrics). Context may be empty if
    nothing to gather. Metrics is None when parallel gathering is disabled.
    """
    files_to_read = plan.get("files_to_read", [])
    grep_patterns = plan.get("grep_patterns", [])
    urls_to_fetch = plan.get("urls_to_fetch", [])

    if not files_to_read and not grep_patterns and not urls_to_fetch:
        return "", None

    all_tasks = _plan_to_tasks(plan)

    if not PARALLEL_GATHERING_ENABLED:
        # Sequential fallback — execute tasks one by one
        gather_results: list[GatherResult] = []
        for task in all_tasks:
            if cancellation_event and cancellation_event.is_set():
                break
            r = await _execute_single_task(task, tool_registry, tool_context, repo_root)
            gather_results.append(r)
        context_bundle = _format_results(gather_results, all_tasks)
        logger.info("Gather (sequential): collected %d results", len(gather_results))
        return context_bundle, None

    # Parallel execution
    wall_start = time.monotonic()
    independent, dependent = partition_by_dependencies(all_tasks)

    result_map: dict[str, GatherResult] = {}

    # Check cancellation before starting
    if cancellation_event and cancellation_event.is_set():
        return "", None

    # Run independent tasks in parallel (with per-domain limiting for web fetches)
    if independent:
        ind_results = await asyncio.gather(*[
            _execute_with_domain_limit(t, tool_registry, tool_context, repo_root)
            for t in independent
        ])
        for r in ind_results:
            result_map[r.task_name] = r

    # Run dependent tasks in tiers
    dep_results: list[GatherResult] = []
    if dependent:
        dep_results = await _execute_dependent_tasks(
            dependent, result_map, tool_registry, tool_context,
            repo_root, cancellation_event,
        )

    wall_ms = (time.monotonic() - wall_start) * 1000
    all_results = list(result_map.values()) + dep_results
    # Deduplicate (dep_results are also in result_map)
    seen_names: set[str] = set()
    unique_results: list[GatherResult] = []
    for r in all_results:
        if r.task_name not in seen_names:
            seen_names.add(r.task_name)
            unique_results.append(r)

    sequential_equivalent = sum(r.elapsed_ms for r in unique_results)
    timed_out = sum(1 for r in unique_results if r.error and "Timeout" in r.content)
    failed = sum(1 for r in unique_results if r.error)

    metrics = GatherMetrics(
        total_tasks=len(all_tasks),
        parallel_tasks=len(independent),
        sequential_tasks=len(dependent),
        wall_clock_ms=wall_ms,
        sequential_equivalent_ms=sequential_equivalent,
        speedup=sequential_equivalent / wall_ms if wall_ms > 0 else 1.0,
        tasks_timed_out=timed_out,
        tasks_failed=failed,
    )

    logger.info(
        "Gather: %d tasks (parallel=%d, sequential=%d), wall=%.0fms, "
        "speedup=%.1fx, timed_out=%d, failed=%d",
        metrics.total_tasks, metrics.parallel_tasks, metrics.sequential_tasks,
        metrics.wall_clock_ms, metrics.speedup,
        metrics.tasks_timed_out, metrics.tasks_failed,
    )

    context_bundle = _format_results(unique_results, all_tasks)
    return context_bundle, metrics


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
        return min(max_iterations, 20)
    elif complexity == "simple":
        return min(max_iterations, 12)
    elif complexity == "moderate":
        return min(max_iterations, 48)
    elif complexity == "complex":
        return min(max_iterations, 80)

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
