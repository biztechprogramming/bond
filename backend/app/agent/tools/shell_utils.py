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

    Searches EVERY word in the query independently (OR logic) and matches
    against: filenames, directory/path components at any depth, file contents,
    and project/directory names. Returns full paths with a preview snippet
    so the agent can confirm the right file without a follow-up read.

    This is the preferred tool for "find me X" requests. Use shell_find or
    shell_grep only when you need their specific features (glob patterns,
    regex context lines, etc.).
    """
    query = arguments.get("query", "")
    path = arguments.get("path", "/workspace")
    file_type = arguments.get("type")  # f, d — optional filter
    include = arguments.get("include")  # e.g. "*.md", "*.py"
    max_results = arguments.get("max_results", 30)
    preview_chars = 200  # first N chars of each matched file

    if not query:
        return {"error": "query is required"}

    import re

    numbers = re.findall(r'\d+', query)
    words = [w.lower() for w in re.findall(r'[a-zA-Z]+', query) if len(w) > 2]

    # Collect all unique file paths across strategies, then enrich at the end
    seen: set[str] = set()
    filename_matches: list[str] = []
    path_matches: list[str] = []
    content_matches: list[str] = []

    def _add(target: list[str], files: list[str]) -> None:
        for f in files:
            if f and f not in seen:
                seen.add(f)
                target.append(f)

    # Build exclusion clause
    prune_clause = r"\( -name .venv -o -name node_modules -o -name __pycache__ -o -name .git -o -name .next -o -name dist -o -name build -o -name .tox \) -prune -o"
    type_clause = f"-type {file_type}" if file_type in ("f", "d") else "-type f"

    # --- Build search tokens ---
    filename_patterns: set[str] = set()
    for word in words:
        filename_patterns.add(f"*{word}*")
    for num in numbers:
        filename_patterns.add(f"*{num}*")
        if len(num) < 3:
            filename_patterns.add(f"*{num.zfill(3)}*")  # 27 → 027
    filename_patterns_list = list(filename_patterns)[:10]

    # --- Strategy 1: Filename matching (always runs) ---
    if filename_patterns_list:
        name_preds = " -o ".join(f'-iname {_shell_quote(p)}' for p in filename_patterns_list)

        if include:
            # Match include extension AND any name pattern
            grep_patterns = "|".join(
                p.replace("*", ".*") for p in filename_patterns_list
            )
            find_cmd = (
                f"find {_shell_quote(path)} {prune_clause} {type_clause} "
                f"-iname {_shell_quote(include)} -print 2>/dev/null "
                f"| grep -iE '{grep_patterns}' | head -{max_results}"
            )
        else:
            find_cmd = (
                f"find {_shell_quote(path)} {prune_clause} {type_clause} "
                f"\\( {name_preds} \\) -print 2>/dev/null | sort | head -{max_results}"
            )

        find_result = await _run_cmd(["sh", "-c", find_cmd], timeout=10)
        if find_result["exit_code"] == 0 and find_result["stdout"].strip():
            _add(filename_matches, find_result["stdout"].strip().split("\n"))

    # --- Strategy 2: Full path matching (always runs) ---
    # Match query words against ANY path component (parent dirs, grandparent, etc.)
    # e.g. "inspection defect" matches /app/inspection/components/DefectEntry.razor
    if words:
        # Find all files, then filter paths that contain ANY query word
        path_grep_pattern = "|".join(re.escape(w) for w in words)
        for num in numbers:
            path_grep_pattern += "|" + re.escape(num)
            if len(num) < 3:
                path_grep_pattern += "|" + re.escape(num.zfill(3))

        include_filter = f"-iname {_shell_quote(include)}" if include else ""
        path_cmd = (
            f"find {_shell_quote(path)} {prune_clause} {type_clause} "
            f"{include_filter} -print 2>/dev/null "
            f"| grep -iE '{path_grep_pattern}' | sort | head -{max_results * 2}"
        )
        path_result = await _run_cmd(["sh", "-c", path_cmd], timeout=15)
        if path_result["exit_code"] == 0 and path_result["stdout"].strip():
            _add(path_matches, path_result["stdout"].strip().split("\n"))

    # --- Strategy 3: Content search (always runs) ---
    # Search file contents for ALL query terms (each word independently via OR)
    grep_terms: list[str] = []
    for num in numbers:
        grep_terms.append(num)
        if len(num) < 3:
            grep_terms.append(num.zfill(3))
    sorted_words = sorted(words, key=len, reverse=True)[:5]
    grep_terms.extend(sorted_words)

    if grep_terms:
        grep_pattern = "\\|".join(grep_terms)
        # Search all common file types when no include filter is specified
        if include:
            include_flags = f"--include={_shell_quote(include)}"
        else:
            include_flags = (
                "--include='*.md' --include='*.txt' --include='*.yaml' --include='*.yml' "
                "--include='*.py' --include='*.ts' --include='*.tsx' --include='*.js' "
                "--include='*.jsx' --include='*.cs' --include='*.razor' --include='*.cshtml' "
                "--include='*.html' --include='*.css' --include='*.scss' --include='*.json' "
                "--include='*.xml' --include='*.toml' --include='*.cfg' --include='*.ini' "
                "--include='*.java' --include='*.go' --include='*.rs' --include='*.rb' "
                "--include='*.php' --include='*.swift' --include='*.kt' --include='*.c' "
                "--include='*.cpp' --include='*.h' --include='*.hpp' --include='*.sql' "
                "--include='*.sh' --include='*.bash' --include='*.zsh' --include='*.vue' "
                "--include='*.svelte' --include='*.astro' --include='*.tf' --include='*.hcl' "
                "--include='*.proto' --include='*.graphql' --include='*.prisma' "
                "--include='*.csproj' --include='*.sln' --include='*.fsproj'"
            )

        grep_cmd = (
            f"grep -rnil {include_flags} "
            f"--exclude-dir=.venv --exclude-dir=node_modules "
            f"--exclude-dir=__pycache__ --exclude-dir=.git --exclude-dir=.next "
            f"--exclude-dir=dist --exclude-dir=build --exclude-dir=.tox "
            f"--exclude-dir=bin --exclude-dir=obj "
            f"{_shell_quote(grep_pattern)} {_shell_quote(path)} 2>/dev/null "
            f"| head -{max_results}"
        )
        grep_result = await _run_cmd(["sh", "-c", grep_cmd], timeout=15)
        if grep_result["exit_code"] == 0 and grep_result["stdout"].strip():
            _add(content_matches, grep_result["stdout"].strip().split("\n"))

    # --- Enrich results with previews ---
    all_files = filename_matches + path_matches + content_matches
    enriched: list[dict[str, str]] = []
    for filepath in all_files[:max_results]:
        entry: dict[str, str] = {"path": filepath}
        # Add file size + preview
        try:
            stat_cmd = f"head -c {preview_chars} {_shell_quote(filepath)} 2>/dev/null"
            preview_result = await _run_cmd(["sh", "-c", stat_cmd], timeout=3)
            if preview_result["exit_code"] == 0:
                preview_text = preview_result["stdout"]
                # Replace excessive whitespace for readability
                preview_text = re.sub(r'\n{3,}', '\n\n', preview_text).strip()
                entry["preview"] = preview_text
        except Exception:
            pass
        enriched.append(entry)

    # Build result
    results: dict[str, Any] = {
        "query": query,
        "search_root": path,
        "filename_matches": [e for e in enriched if e["path"] in set(filename_matches)],
        "path_matches": [e for e in enriched if e["path"] in set(path_matches)],
        "content_matches": [e for e in enriched if e["path"] in set(content_matches)],
        "total_results": len(enriched),
    }

    if len(enriched) == 0:
        results["suggestion"] = (
            f"No results found for '{query}'. Try: "
            f"shell_ls on likely directories, or shell_grep with simpler patterns."
        )

    return results
