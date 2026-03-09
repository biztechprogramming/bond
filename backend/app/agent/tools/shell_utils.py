"""Lightweight shell utility tools.

These replace common code_execute patterns (find, ls, grep, git status, etc.)
with dedicated, schema-driven tools. Benefits:

1. **Cheaper model routing** — these are info-gathering tools that qualify for
   the utility model, saving primary model calls.
2. **Better schema constraints** — the model fills structured parameters
   instead of composing arbitrary shell commands.
3. **Safer** — restricted to read-only operations with argument validation.

All handlers follow the same (arguments, context) → dict signature as other
native tools.

See also: handle_project_search — a high-level search that combines filename
matching, content search, and fuzzy matching in one call (Doc 029).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("bond.agent.tools.shell_utils")

# Max output bytes to keep context lean
_MAX_OUTPUT_BYTES = 10_000

# Default working directory
_DEFAULT_CWD = "/workspace"

# Disallowed patterns in paths (prevent traversal attacks)
_BLOCKED_PATH_PATTERNS = frozenset({"/../", "/..", "../"})


def _shell_quote(s: str) -> str:
    """Shell-quote a string to prevent injection."""
    import shlex
    return shlex.quote(s)


def _safe_cwd() -> str:
    """Return the current working directory, falling back to _DEFAULT_CWD."""
    try:
        cwd = os.getcwd()
        return cwd if os.path.isdir(cwd) else _DEFAULT_CWD
    except OSError:
        return _DEFAULT_CWD


def _truncate(text: str, max_bytes: int = _MAX_OUTPUT_BYTES) -> str:
    """Truncate output to max_bytes with a notice."""
    if len(text) <= max_bytes:
        return text
    return text[:max_bytes] + f"\n[output truncated at {max_bytes // 1000} KB]"


async def _run_cmd(
    cmd: list[str],
    cwd: str | None = None,
    timeout: int = 15,
) -> dict[str, Any]:
    """Run a command and return stdout/stderr/exit_code."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or _safe_cwd(),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {
            "stdout": _truncate(stdout.decode(errors="replace")),
            "stderr": stderr.decode(errors="replace")[:2000],
            "exit_code": proc.returncode,
        }
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return {"stdout": "", "stderr": "Command timed out", "exit_code": -1}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "exit_code": -1}


# ---------------------------------------------------------------------------
# find — locate files by name, pattern, or type
# ---------------------------------------------------------------------------

async def handle_shell_find(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Find files matching name/pattern/type criteria."""
    path = arguments.get("path", ".")
    name = arguments.get("name")          # exact name or glob pattern
    regex = arguments.get("regex")        # regex pattern for filename/path
    type_ = arguments.get("type")         # f=file, d=directory
    max_depth = arguments.get("max_depth")
    exclude = arguments.get("exclude", [])  # patterns to exclude

    # Common exclusions: .venv, node_modules, __pycache__, .git
    prune_dirs = [".venv", "node_modules", "__pycache__", ".git"]
    if exclude:
        prune_dirs.extend(exclude)

    # Build command: find <path> [-maxdepth N] <prune_expr> <predicates> -print
    parts = ["find", _shell_quote(path)]

    if max_depth is not None:
        parts += ["-maxdepth", str(int(max_depth))]

    # Prune expression must come before other predicates
    prune_names = " -o ".join(f"-name {_shell_quote(d)}" for d in prune_dirs)
    parts.append(f"\\( {prune_names} \\) -prune -o")

    # Match predicates
    predicates = []
    if type_ in ("f", "d", "l"):
        predicates += ["-type", type_]
    if name:
        predicates += ["-iname", _shell_quote(name)]  # case-insensitive by default
    elif regex:
        predicates += ["-regextype", "posix-extended", "-iregex", _shell_quote(regex)]

    if predicates:
        parts.extend(predicates)

    parts.append("-print")

    shell_cmd = " ".join(parts) + " | sort"
    result = await _run_cmd(["sh", "-c", shell_cmd])

    if result["exit_code"] == 0:
        lines = [l for l in result["stdout"].strip().split("\n") if l]
        return {
            "files": lines[:500],  # cap at 500 entries
            "count": len(lines),
            "truncated": len(lines) > 500,
        }
    return result


# ---------------------------------------------------------------------------
# ls — list directory contents
# ---------------------------------------------------------------------------

async def handle_shell_ls(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """List directory contents with optional details."""
    path = arguments.get("path", ".")
    long_format = arguments.get("long", False)
    all_files = arguments.get("all", False)

    cmd = ["ls"]
    if long_format:
        cmd.append("-lh")
    if all_files:
        cmd.append("-a")
    cmd.append(path)

    return await _run_cmd(cmd)


# ---------------------------------------------------------------------------
# grep — search text patterns
# ---------------------------------------------------------------------------

async def handle_shell_grep(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Search for text patterns in files."""
    pattern = arguments.get("pattern", "")
    path = arguments.get("path", ".")
    recursive = arguments.get("recursive", True)
    include = arguments.get("include")       # e.g. "*.py"
    ignore_case = arguments.get("ignore_case", False)
    context_lines = arguments.get("context_lines", 0)  # -A/-B equivalent
    max_count = arguments.get("max_count")    # --max-count per file

    if not pattern:
        return {"error": "pattern is required"}

    cmd = ["grep", "-n"]  # always show line numbers
    if recursive:
        cmd.append("-r")
    if ignore_case:
        cmd.append("-i")
    if context_lines > 0:
        cmd += [f"-C{int(context_lines)}"]
    if max_count is not None:
        cmd += [f"--max-count={int(max_count)}"]
    if include:
        cmd += [f"--include={include}"]

    # Always exclude common noise
    cmd += [
        "--exclude-dir=.venv",
        "--exclude-dir=node_modules",
        "--exclude-dir=__pycache__",
        "--exclude-dir=.git",
    ]

    cmd += [pattern, path]

    result = await _run_cmd(cmd, timeout=20)

    # grep returns exit code 1 for "no matches" — that's not an error
    if result["exit_code"] == 1 and not result["stderr"]:
        return {"matches": [], "count": 0}

    if result["exit_code"] == 0:
        lines = result["stdout"].strip().split("\n") if result["stdout"].strip() else []
        return {
            "matches": lines[:200],  # cap displayed matches
            "count": len(lines),
            "truncated": len(lines) > 200,
        }

    return result


# ---------------------------------------------------------------------------
# git_info — read-only git operations (status, log, diff, branch)
# ---------------------------------------------------------------------------

async def handle_git_info(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Execute read-only git operations."""
    action = arguments.get("action", "status")

    if action == "status":
        return await _run_cmd(["git", "status", "--short", "--branch"])

    elif action == "log":
        n = min(arguments.get("count", 10), 50)
        format_ = arguments.get("format", "oneline")
        cmd = ["git", "log", f"-{n}"]
        if format_ == "oneline":
            cmd.append("--oneline")
        elif format_ == "full":
            cmd += ["--format=%H %an %ai %s"]
        return await _run_cmd(cmd)

    elif action == "diff":
        target = arguments.get("target", "")
        cmd = ["git", "diff", "--stat"]
        if target:
            cmd.append(target)
        return await _run_cmd(cmd)

    elif action == "branch":
        return await _run_cmd(["git", "branch", "-vv"])

    elif action == "show":
        ref = arguments.get("ref", "HEAD")
        cmd = ["git", "show", "--stat", ref]
        return await _run_cmd(cmd)

    else:
        return {"error": f"Unknown git action: {action}. Use: status, log, diff, branch, show"}


# ---------------------------------------------------------------------------
# wc — count lines/words/chars
# ---------------------------------------------------------------------------

async def handle_shell_wc(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Count lines, words, or characters in files."""
    path = arguments.get("path", "")
    mode = arguments.get("mode", "lines")  # lines, words, chars

    if not path:
        return {"error": "path is required"}

    flag = "-l" if mode == "lines" else "-w" if mode == "words" else "-c"
    return await _run_cmd(["wc", flag, path])


# ---------------------------------------------------------------------------
# head/tail — view start or end of files
# ---------------------------------------------------------------------------

async def handle_shell_head(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """View the first or last N lines of a file."""
    path = arguments.get("path", "")
    lines = arguments.get("lines", 20)
    from_end = arguments.get("from_end", False)  # if True, use tail

    if not path:
        return {"error": "path is required"}

    cmd_name = "tail" if from_end else "head"
    return await _run_cmd([cmd_name, f"-n{int(lines)}", path])


# ---------------------------------------------------------------------------
# tree — directory structure view
# ---------------------------------------------------------------------------

async def handle_shell_tree(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Show directory tree structure."""
    path = arguments.get("path", ".")
    max_depth = arguments.get("max_depth", 3)
    dirs_only = arguments.get("dirs_only", False)

    parts = [
        "find", _shell_quote(path),
        "-maxdepth", str(int(max_depth)),
        r"\( -name .venv -o -name node_modules -o -name __pycache__ -o -name .git \) -prune -o",
    ]
    if dirs_only:
        parts += ["-type", "d"]
    parts.append("-print")

    shell_cmd = " ".join(parts) + " | sort"
    return await _run_cmd(["sh", "-c", shell_cmd])


# ---------------------------------------------------------------------------
# project_search — intelligent multi-strategy file/content search (Doc 029)
# ---------------------------------------------------------------------------

async def handle_project_search(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Search the project using multiple strategies simultaneously.

    Combines filename matching, content search, and path component matching
    into a single tool call. Returns categorized results so the agent can
    immediately act on what was found.

    This is the preferred tool for "find me X" requests. Use shell_find or
    shell_grep only when you need their specific features (glob patterns,
    regex context lines, etc.).
    """
    query = arguments.get("query", "")
    path = arguments.get("path", "/workspace")
    file_type = arguments.get("type")  # f, d — optional filter
    include = arguments.get("include")  # e.g. "*.md", "*.py"
    max_results = arguments.get("max_results", 30)

    if not query:
        return {"error": "query is required"}

    results: dict[str, Any] = {
        "query": query,
        "search_root": path,
        "filename_matches": [],
        "content_matches": [],
        "path_matches": [],
    }

    # Build exclusion clause
    prune_clause = r"\( -name .venv -o -name node_modules -o -name __pycache__ -o -name .git -o -name .next \) -prune -o"
    type_clause = f"-type {file_type}" if file_type in ("f", "d") else "-type f"

    # --- Strategy 1: Filename matching ---
    # Try multiple patterns: exact, contains, numeric prefix, fuzzy
    # Extract numeric component if present (e.g. "design doc 27" → "027", "27")
    import re
    numbers = re.findall(r'\d+', query)
    words = [w.lower() for w in re.findall(r'[a-zA-Z]+', query) if len(w) > 2]

    filename_patterns = set()

    # Direct glob from query words
    for word in words:
        filename_patterns.add(f"*{word}*")

    # Numeric patterns: with and without zero-padding
    for num in numbers:
        filename_patterns.add(f"*{num}*")
        if len(num) < 3:
            filename_patterns.add(f"*{num.zfill(3)}*")  # 27 → 027

    # Limit patterns to avoid explosion
    filename_patterns = list(filename_patterns)[:8]

    if filename_patterns:
        # Build a single find command with -o for all patterns
        name_preds = " -o ".join(f'-iname {_shell_quote(p)}' for p in filename_patterns)
        include_filter = f'-iname {_shell_quote(include)}' if include else ""

        if include_filter:
            # Two-stage: match include pattern AND name pattern
            find_cmd = (
                f"find {_shell_quote(path)} {prune_clause} {type_clause} "
                f"{include_filter} -print 2>/dev/null | head -1000"
            )
            # Filter the include results by name patterns
            grep_patterns = "|".join(
                p.replace("*", ".*") for p in filename_patterns
            )
            find_cmd = (
                f"find {_shell_quote(path)} {prune_clause} {type_clause} "
                f"{include_filter} -print 2>/dev/null | grep -iE '{grep_patterns}' | head -{max_results}"
            )
        else:
            find_cmd = (
                f"find {_shell_quote(path)} {prune_clause} {type_clause} "
                f"\\( {name_preds} \\) -print 2>/dev/null | sort | head -{max_results}"
            )

        find_result = await _run_cmd(["sh", "-c", find_cmd], timeout=10)
        if find_result["exit_code"] == 0 and find_result["stdout"].strip():
            files = [f for f in find_result["stdout"].strip().split("\n") if f]
            results["filename_matches"] = files[:max_results]

    # --- Strategy 2: Content search (grep) ---
    # Search file contents for the query terms — useful when the filename
    # doesn't match but the content contains what the user is looking for.
    # Only run if filename search found < 5 results (otherwise we probably
    # already have what we need).
    if len(results["filename_matches"]) < 5:
        # Build a grep pattern from the most distinctive query terms
        grep_terms = []
        for num in numbers:
            grep_terms.append(num)
            if len(num) < 3:
                grep_terms.append(num.zfill(3))
        # Add the longest words (most distinctive)
        sorted_words = sorted(words, key=len, reverse=True)[:3]
        grep_terms.extend(sorted_words)

        if grep_terms:
            grep_pattern = "\\|".join(grep_terms)
            include_flag = f"--include={_shell_quote(include)}" if include else "--include='*.md' --include='*.txt' --include='*.yaml' --include='*.yml'"

            grep_cmd = (
                f"grep -rnil {include_flag} "
                f"--exclude-dir=.venv --exclude-dir=node_modules "
                f"--exclude-dir=__pycache__ --exclude-dir=.git --exclude-dir=.next "
                f"{_shell_quote(grep_pattern)} {_shell_quote(path)} 2>/dev/null "
                f"| head -{max_results}"
            )
            grep_result = await _run_cmd(["sh", "-c", grep_cmd], timeout=15)
            if grep_result["exit_code"] == 0 and grep_result["stdout"].strip():
                content_files = [f for f in grep_result["stdout"].strip().split("\n") if f]
                # Deduplicate against filename matches
                already_found = set(results["filename_matches"])
                results["content_matches"] = [
                    f for f in content_files if f not in already_found
                ][:max_results]

    # --- Strategy 3: Path component matching ---
    # Search for query terms in directory names (e.g. "design" matches docs/design/)
    if words and len(results["filename_matches"]) + len(results["content_matches"]) < 5:
        # Find directories matching query words, then list their contents
        dir_patterns = " -o ".join(f'-iname {_shell_quote("*" + w + "*")}' for w in words[:3])
        dir_cmd = (
            f"find {_shell_quote(path)} -maxdepth 4 "
            f"{prune_clause} -type d \\( {dir_patterns} \\) -print 2>/dev/null | head -5"
        )
        dir_result = await _run_cmd(["sh", "-c", dir_cmd], timeout=10)
        if dir_result["exit_code"] == 0 and dir_result["stdout"].strip():
            matching_dirs = [d for d in dir_result["stdout"].strip().split("\n") if d]
            for d in matching_dirs[:3]:
                # List contents of matching directories
                ls_cmd = f"find {_shell_quote(d)} -maxdepth 1 -type f -print 2>/dev/null | sort"
                ls_result = await _run_cmd(["sh", "-c", ls_cmd], timeout=5)
                if ls_result["exit_code"] == 0 and ls_result["stdout"].strip():
                    dir_files = [f for f in ls_result["stdout"].strip().split("\n") if f]
                    already_found = set(results["filename_matches"] + results["content_matches"])
                    new_files = [f for f in dir_files if f not in already_found]
                    results["path_matches"].extend(new_files[:10])

    # Build summary
    total = len(results["filename_matches"]) + len(results["content_matches"]) + len(results["path_matches"])
    results["total_results"] = total

    if total == 0:
        results["suggestion"] = (
            f"No results found for '{query}'. Try: "
            f"shell_ls on likely directories, or shell_grep with simpler patterns."
        )

    return results
