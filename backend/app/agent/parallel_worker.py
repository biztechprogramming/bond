"""Parallel Worker Pool — spawns utility model workers for concurrent info gathering.

Classifies tool calls as parallel-safe (information gathering) or consequential,
spawns utility model workers for parallel-safe calls, and aggregates results
for the primary model to review.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

logger = logging.getLogger("bond.agent.parallel_worker")

# ── Tool Classification ──────────────────────────────────────────────────────

# Tools that are always safe to run in parallel (read-only / info gathering)
ALWAYS_PARALLEL_SAFE = frozenset({
    "file_read",
    "web_search",
    "web_read",
    "search_memory",
    "load_context",
    "project_search",
    "file_list",
})

# Tools that are always consequential (must run sequentially via primary model)
ALWAYS_CONSEQUENTIAL = frozenset({
    "file_write",
    "file_edit",
    "memory_save",
    "memory_update",
    "memory_delete",
    "respond",
    "work_plan",
    "repo_pr",
    "call_subordinate",
    "email",
    "cron",
    "notify",
    "skills",
    "browser",
})

# code_execute needs heuristic analysis
NEEDS_ANALYSIS = frozenset({"code_execute"})

# ── Read-only command detection ──────────────────────────────────────────────

_READ_ONLY_PATTERNS = [
    re.compile(r"^(cat|head|tail|less|more)\s"),
    re.compile(r"^(ls|dir|find|locate)\s"),
    re.compile(r"^(ls|pwd|whoami|hostname|uname)$"),
    re.compile(r"^(grep|rg|ag|ack|fgrep|egrep)\s"),
    re.compile(r"^(wc|du|df|stat|file|which|type|whereis)\s"),
    re.compile(r"^git\s+(log|status|diff|show|branch|tag|remote|rev-parse|describe)"),
    re.compile(r"^(echo|printf)\s"),
    re.compile(r"^(python3?|node|ruby)\s+-c\s+"),
    re.compile(r"^sed\s+-n\s"),
    re.compile(r"^(tree|realpath|readlink|basename|dirname)\s"),
    re.compile(r"^(env|printenv|set)$"),
    re.compile(r"^(date|cal|uptime|free|top\s+-bn1)"),
    re.compile(r"^(jq|yq|xmllint)\s"),
    re.compile(r"^(curl|wget)\s.*-[sS]"),  # silent fetch (read-only intent)
]

_MUTATING_PATTERNS = [
    re.compile(r"^(rm|mv|cp|mkdir|touch|chmod|chown|ln)\s"),
    re.compile(r"^(pip|pip3|npm|yarn|pnpm|cargo|apt|apt-get|brew|dnf|yum)\s+install"),
    re.compile(r"^git\s+(commit|push|pull|merge|rebase|checkout|reset|stash|cherry-pick|am|apply)"),
    re.compile(r"^(docker|kubectl|helm|terraform)\s"),
    re.compile(r"[>|]\s*\S"),  # Output redirection or piping to a command
    re.compile(r"^(make|cmake|cargo\s+build|go\s+build|mvn|gradle)"),
    re.compile(r"^(systemctl|service|supervisorctl)\s"),
    re.compile(r"^(kill|pkill|killall)\s"),
    re.compile(r"^(sed|awk)\s+-i"),  # In-place edit
    re.compile(r"^(tee)\s"),
]


def _is_read_only_command(code: str) -> bool:
    """Heuristically determine if a code_execute call is read-only.
    
    Returns True if the command appears to only read data.
    Returns False (conservative) for anything uncertain.
    """
    if not code or not code.strip():
        return False

    # Check each line of a multi-line command
    lines = [l.strip() for l in code.strip().split("\n") if l.strip() and not l.strip().startswith("#")]
    
    if not lines:
        return False

    for line in lines:
        # Check against mutating patterns first (higher priority)
        for pattern in _MUTATING_PATTERNS:
            if pattern.search(line):
                return False

    # Check if ALL lines match read-only patterns
    for line in lines:
        matched = False
        for pattern in _READ_ONLY_PATTERNS:
            if pattern.search(line):
                matched = True
                break
        
        # Also allow chained commands where each part is read-only
        if not matched and "&&" in line:
            parts = [p.strip() for p in line.split("&&")]
            all_safe = all(
                any(p.search(part) for p in _READ_ONLY_PATTERNS)
                for part in parts if part
            )
            if all_safe:
                matched = True
        
        if not matched:
            return False

    return True


def classify_tool_call(tool_name: str, arguments: dict[str, Any]) -> str:
    """Classify a tool call as 'parallel' or 'consequential'.
    
    Returns:
        'parallel' — safe to run concurrently with utility model
        'consequential' — must run sequentially with primary model oversight
    """
    if tool_name in ALWAYS_PARALLEL_SAFE:
        return "parallel"
    
    if tool_name in ALWAYS_CONSEQUENTIAL:
        return "consequential"
    
    if tool_name in NEEDS_ANALYSIS:
        code = arguments.get("code", "")
        language = arguments.get("language", "shell")
        
        # Python code_execute is harder to classify — be conservative
        if language == "python":
            # Simple read-only python patterns
            code_stripped = code.strip()
            if any(kw in code_stripped for kw in ["open(", "write(", "os.remove", "shutil.", "subprocess.run"]):
                return "consequential"
            # If it's a short read-only script, allow parallel
            if len(code_stripped) < 200 and "print(" in code_stripped:
                return "parallel"
            return "consequential"
        
        # Shell commands — use pattern matching
        if _is_read_only_command(code):
            return "parallel"
        
        return "consequential"
    
    # Unknown tools — be conservative
    return "consequential"


# ── Parallel Worker Pool ─────────────────────────────────────────────────────

class ParallelWorkerPool:
    """Manages concurrent execution of utility model workers.
    
    Usage:
        pool = ParallelWorkerPool(registry, utility_model, utility_kwargs, context)
        results = await pool.execute(tool_calls, tool_call_objects)
    
    The pool:
    1. Classifies each tool call as parallel or consequential
    2. Executes all parallel calls concurrently via asyncio.gather
    3. Returns results in original order, with consequential calls marked for
       sequential execution by the caller
    """

    def __init__(
        self,
        registry: Any,  # ToolRegistry
        utility_model: str,
        utility_kwargs: dict[str, Any],
        context: dict[str, Any],
        max_workers: int = 10,
        timeout_per_worker: float = 30.0,
    ):
        self.registry = registry
        self.utility_model = utility_model
        self.utility_kwargs = utility_kwargs
        self.context = context
        self.max_workers = max_workers
        self.timeout_per_worker = timeout_per_worker
        self._semaphore = asyncio.Semaphore(max_workers)

    async def _execute_single(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        index: int,
    ) -> dict[str, Any]:
        """Execute a single tool call within the semaphore limit."""
        async with self._semaphore:
            start = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    self.registry.execute(tool_name, arguments, self.context),
                    timeout=self.timeout_per_worker,
                )
                elapsed = time.monotonic() - start
                logger.info(
                    "Parallel worker %d completed %s in %.2fs",
                    index, tool_name, elapsed,
                )
                return {
                    "index": index,
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "result": result,
                    "elapsed": elapsed,
                    "status": "success",
                }
            except asyncio.TimeoutError:
                elapsed = time.monotonic() - start
                logger.warning(
                    "Parallel worker %d timed out on %s after %.2fs",
                    index, tool_name, elapsed,
                )
                return {
                    "index": index,
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "result": {"error": f"Worker timed out after {self.timeout_per_worker}s"},
                    "elapsed": elapsed,
                    "status": "timeout",
                }
            except Exception as e:
                elapsed = time.monotonic() - start
                logger.exception(
                    "Parallel worker %d failed on %s: %s",
                    index, tool_name, e,
                )
                return {
                    "index": index,
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "result": {"error": str(e)},
                    "elapsed": elapsed,
                    "status": "error",
                }

    async def execute(
        self,
        tool_calls: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Execute tool calls with maximum parallelism.
        
        Args:
            tool_calls: List of dicts with keys:
                - tool_call_id: str
                - tool_name: str  
                - arguments: dict
        
        Returns:
            Tuple of (parallel_results, consequential_calls):
            - parallel_results: completed results from parallel execution
            - consequential_calls: tool calls that need sequential execution
        """
        parallel_batch: list[tuple[int, dict]] = []
        consequential_batch: list[dict] = []

        # Phase 1: Classify
        for i, tc in enumerate(tool_calls):
            tool_name = tc["tool_name"]
            arguments = tc.get("arguments", {})
            classification = classify_tool_call(tool_name, arguments)

            if classification == "parallel":
                parallel_batch.append((i, tc))
            else:
                consequential_batch.append(tc)

        logger.info(
            "Tool call classification: %d parallel, %d consequential (total: %d)",
            len(parallel_batch), len(consequential_batch), len(tool_calls),
        )

        # Phase 2: Execute parallel batch concurrently
        parallel_results: list[dict[str, Any]] = []
        if parallel_batch:
            start = time.monotonic()
            tasks = [
                self._execute_single(tc["tool_name"], tc.get("arguments", {}), idx)
                for idx, tc in parallel_batch
            ]
            raw_results = await asyncio.gather(*tasks, return_exceptions=True)
            elapsed = time.monotonic() - start

            for j, raw in enumerate(raw_results):
                if isinstance(raw, Exception):
                    idx, tc = parallel_batch[j]
                    parallel_results.append({
                        "index": idx,
                        "tool_call_id": tc.get("tool_call_id"),
                        "tool_name": tc["tool_name"],
                        "result": {"error": str(raw)},
                        "status": "error",
                    })
                else:
                    # Attach the tool_call_id from the original call
                    raw["tool_call_id"] = parallel_batch[j][1].get("tool_call_id")
                    parallel_results.append(raw)

            logger.info(
                "Parallel batch of %d calls completed in %.2fs",
                len(parallel_batch), elapsed,
            )

        return parallel_results, consequential_batch


def build_tool_result_message(tool_call_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """Build a tool result message for the conversation history."""
    content = json.dumps(result, default=str)
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": content,
    }


def format_parallel_summary(parallel_results: list[dict]) -> str:
    """Create a human-readable summary of parallel execution for logging."""
    if not parallel_results:
        return "No parallel results"
    
    lines = [f"Parallel execution summary ({len(parallel_results)} workers):"]
    total_time = 0.0
    for pr in parallel_results:
        elapsed = pr.get("elapsed", 0)
        total_time += elapsed
        status = pr.get("status", "unknown")
        tool = pr.get("tool_name", "unknown")
        lines.append(f"  [{status}] {tool} — {elapsed:.2f}s")
    
    if len(parallel_results) > 1:
        max_time = max(pr.get("elapsed", 0) for pr in parallel_results)
        saved = total_time - max_time
        lines.append(f"  Wall time: ~{max_time:.2f}s (saved ~{saved:.2f}s via parallelism)")
    
    return "\n".join(lines)
