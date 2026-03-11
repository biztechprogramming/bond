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
    truncate: bool = True,
) -> dict[str, Any]:
    """Run a command and return stdout/stderr/exit_code.

    Set truncate=False for internal helpers (like file listing) where the
    full output is processed in Python and never sent to the LLM.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or _safe_cwd(),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        stdout_str = stdout.decode(errors="replace")
        return {
            "stdout": _truncate(stdout_str) if truncate else stdout_str,
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


async def _get_file_listing(path: str, file_type: str | None, include: str | None) -> list[str]:
    """Get file listing respecting .gitignore when inside a git repo.

    Uses `git ls-files` (tracked + untracked-but-not-ignored) when inside a
    git repo, falls back to `find` with hardcoded exclusions otherwise.
    Returns absolute paths.
    """
    # Check if we're in a git repo
    git_check = await _run_cmd(
        ["sh", "-c", f"cd {_shell_quote(path)} && git rev-parse --show-toplevel 2>/dev/null"],
        timeout=3,
    )
    is_git = git_check["exit_code"] == 0 and git_check["stdout"].strip()

    if is_git:
        git_root = git_check["stdout"].strip()
        # git ls-files: tracked files + untracked (but not ignored)
        # -c = cached (tracked), -o = others (untracked), --exclude-standard = respect .gitignore
        git_cmd = f"cd {_shell_quote(git_root)} && {{ git ls-files -co --exclude-standard; }} 2>/dev/null"
        result = await _run_cmd(["sh", "-c", git_cmd], timeout=30, truncate=False)
        if result["exit_code"] == 0 and result["stdout"].strip():
            raw_files = result["stdout"].strip().split("\n")
            # Convert to absolute paths
            files = [os.path.join(git_root, f) for f in raw_files if f]
            # Filter to requested path (may be a subdirectory)
            abs_path = os.path.abspath(path)
            files = [f for f in files if f.startswith(abs_path)]
            # Apply type filter
            if file_type == "d":
                # For directories, extract unique parent dirs
                dirs: set[str] = set()
                for f in files:
                    rel = os.path.relpath(f, abs_path)
                    parts = rel.split(os.sep)
                    for i in range(1, len(parts)):
                        dirs.add(os.path.join(abs_path, *parts[:i]))
                files = sorted(dirs)
            # Apply include filter (glob on basename)
            if include:
                import fnmatch
                pattern = include  # e.g. "*.py"
                files = [f for f in files if fnmatch.fnmatch(os.path.basename(f).lower(), pattern.lower())]
            return files
        # git ls-files returned nothing — fall through to find
    # Fallback for non-git directories
    prune_clause = (
        r"\( -name .venv -o -name node_modules -o -name __pycache__ -o -name .git "
        r"-o -name .next -o -name dist -o -name build -o -name .tox "
        r"-o -name bin -o -name obj \) -prune -o"
    )
    type_clause = f"-type {file_type}" if file_type in ("f", "d") else "-type f"
    include_filter = f"-iname {_shell_quote(include)}" if include else ""
    find_cmd = (
        f"find {_shell_quote(path)} {prune_clause} {type_clause} "
        f"{include_filter} -print 2>/dev/null"
    )
    result = await _run_cmd(["sh", "-c", find_cmd], timeout=30, truncate=False)
    if result["exit_code"] == 0 and result["stdout"].strip():
        return result["stdout"].strip().split("\n")
    return []


async def handle_project_search(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Search the project using multiple strategies simultaneously.

    Searches EVERY word in the query independently across filenames, directory
    paths, and file contents. Results are ranked by relevance — files matching
    MORE query terms sort first. Respects .gitignore when inside a git repo
    so ignored files (bin/, obj/, etc.) are never returned.
    """
    query = arguments.get("query", "")
    path = arguments.get("path", "/workspace")
    file_type = arguments.get("type")  # f, d — optional filter
    include = arguments.get("include")  # e.g. "*.md", "*.py"
    max_results = arguments.get("max_results", 30)

    if not query:
        return {"error": "query is required"}

    import re

    numbers = re.findall(r'\d+', query)
    words = [w.lower() for w in re.findall(r'[a-zA-Z]+', query) if len(w) > 2]

    # All search tokens (words + numbers + zero-padded variants)
    all_tokens: list[str] = list(words)
    for num in numbers:
        all_tokens.append(num)
        if len(num) < 3:
            all_tokens.append(num.zfill(3))

    if not all_tokens:
        return {"error": "query produced no searchable tokens"}

    # --- Get file listing (respects .gitignore) ---
    all_project_files = await _get_file_listing(path, file_type, include)

    # --- Score every file by how many tokens match its path ---
    # Each file gets points for tokens matching filename or path components.
    # This is the core ranking: more matched tokens = higher relevance.
    file_scores: dict[str, dict] = {}  # path -> {score, matched_tokens, categories}

    for filepath in all_project_files:
        path_lower = filepath.lower()
        basename_lower = os.path.basename(filepath).lower()
        matched_tokens: set[str] = set()

        for token in all_tokens:
            if token in basename_lower:
                matched_tokens.add(token)
            elif token in path_lower:
                matched_tokens.add(token)

        if matched_tokens:
            # Score: filename matches worth more than path-only matches
            score = 0
            categories: set[str] = set()
            for token in matched_tokens:
                if token in basename_lower:
                    score += 3  # filename match
                    categories.add("filename")
                else:
                    score += 1  # path-only match
                    categories.add("path")
            # Bonus for matching a higher fraction of tokens
            coverage = len(matched_tokens) / len(all_tokens)
            score += int(coverage * 5)
            file_scores[filepath] = {
                "score": score,
                "matched_tokens": matched_tokens,
                "categories": categories,
            }

    # --- Strategy 3: Content search (for files not already found by path) ---
    # Only search content for tokens that might appear inside files but not
    # in the filename/path. This catches references to concepts inside code.
    sorted_words = sorted(words, key=len, reverse=True)[:5]
    grep_terms = list(sorted_words)
    for num in numbers:
        grep_terms.append(num)
        if len(num) < 3:
            grep_terms.append(num.zfill(3))

    if grep_terms:
        grep_pattern = "\\|".join(grep_terms)

        # Check if we're in a git repo for content search too
        git_check = await _run_cmd(
            ["sh", "-c", f"cd {_shell_quote(path)} && git rev-parse --show-toplevel 2>/dev/null"],
            timeout=3,
        )
        is_git = git_check["exit_code"] == 0 and git_check["stdout"].strip()

        if is_git:
            git_root = git_check["stdout"].strip()
            # Use git grep — inherently respects .gitignore
            content_cmd = (
                f"cd {_shell_quote(git_root)} && "
                f"git grep -lni {_shell_quote(grep_pattern)} -- {_shell_quote(path)} 2>/dev/null "
                f"| head -{max_results * 2}"
            )
        else:
            # Fallback: regular grep with hardcoded exclusions
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
            content_cmd = (
                f"grep -rnil {include_flags} "
                f"--exclude-dir=.venv --exclude-dir=node_modules "
                f"--exclude-dir=__pycache__ --exclude-dir=.git --exclude-dir=.next "
                f"--exclude-dir=dist --exclude-dir=build --exclude-dir=.tox "
                f"--exclude-dir=bin --exclude-dir=obj "
                f"{_shell_quote(grep_pattern)} {_shell_quote(path)} 2>/dev/null "
                f"| head -{max_results * 2}"
            )

        grep_result = await _run_cmd(["sh", "-c", content_cmd], timeout=15)
        if grep_result["exit_code"] == 0 and grep_result["stdout"].strip():
            content_files = grep_result["stdout"].strip().split("\n")
            for filepath in content_files:
                if not filepath:
                    continue
                # Make absolute if git grep returned relative paths
                if not os.path.isabs(filepath) and is_git:
                    filepath = os.path.join(git_check["stdout"].strip(), filepath)
                if filepath in file_scores:
                    file_scores[filepath]["score"] += 2
                    file_scores[filepath]["categories"].add("content")
                else:
                    file_scores[filepath] = {
                        "score": 2,
                        "matched_tokens": set(),
                        "categories": {"content"},
                    }

    # --- Rank and enrich top results ---
    ranked_paths = sorted(file_scores.keys(), key=lambda f: file_scores[f]["score"], reverse=True)
    top_files = ranked_paths[:max_results]

    enriched: list[dict[str, Any]] = []
    for filepath in top_files:
        entry: dict[str, Any] = {
            "path": filepath,
            "score": file_scores[filepath]["score"],
            "matched": sorted(file_scores[filepath]["matched_tokens"]),
        }
        # Add file size and last-modified instead of content preview
        try:
            stat_info = os.stat(filepath)
            entry["size"] = stat_info.st_size
            from datetime import datetime, timezone
            entry["modified"] = datetime.fromtimestamp(
                stat_info.st_mtime, tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S UTC")
        except OSError:
            pass
        enriched.append(entry)

    # Backward-compatible category lists (subsets of ranked results)
    filename_set = {e["path"] for e in enriched if "filename" in file_scores.get(e["path"], {}).get("categories", set())}
    path_set = {e["path"] for e in enriched if "path" in file_scores.get(e["path"], {}).get("categories", set())}
    content_set = {e["path"] for e in enriched if "content" in file_scores.get(e["path"], {}).get("categories", set())}

    # --- Wildcard file matches grouped by query term ---
    # Shows what `shell_find -iname "*term*"` would return, so the agent
    # never needs a separate shell_find call.
    wildcard_matches: dict[str, list[str]] = {}
    for token in all_tokens:
        glob_label = f"*{token}*"
        matching_files: list[str] = []
        for filepath in all_project_files:
            if token in os.path.basename(filepath).lower():
                matching_files.append(filepath)
        if matching_files:
            wildcard_matches[glob_label] = sorted(matching_files)

    results: dict[str, Any] = {
        "query": query,
        "search_root": path,
        "results": enriched,  # Primary: ranked by relevance
        "wildcard_matches": wildcard_matches,  # grouped by *term* glob
        "filename_matches": [e for e in enriched if e["path"] in filename_set],
        "path_matches": [e for e in enriched if e["path"] in path_set],
        "content_matches": [e for e in enriched if e["path"] in content_set],
        "total_results": len(enriched),
    }

    if len(enriched) == 0:
        results["suggestion"] = (
            f"No results found for '{query}'. Try: "
            f"shell_ls on likely directories, or shell_grep with simpler patterns."
        )

    return results


# ---------------------------------------------------------------------------
# batch_head — peek at the first N lines of multiple files in one call
# ---------------------------------------------------------------------------


async def handle_batch_head(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Return the first N lines of multiple files in a single tool call.

    Useful after project_search to quickly inspect several candidate files
    without making one tool call per file.
    """
    files = arguments.get("files", [])
    lines = arguments.get("lines", 40)

    if not files:
        return {"error": "files array is required"}
    if not isinstance(files, list):
        return {"error": "files must be an array of file paths"}

    # Cap to avoid runaway output
    max_files = 20
    if len(files) > max_files:
        files = files[:max_files]
    lines = min(max(1, lines), 200)

    results: list[dict[str, Any]] = []
    for filepath in files:
        entry: dict[str, Any] = {"path": filepath}
        result = await _run_cmd(
            ["sh", "-c", f"head -n {lines} {_shell_quote(filepath)} 2>&1"],
            timeout=5,
        )
        if result["exit_code"] == 0:
            entry["content"] = result["stdout"]
            # Include line count for context
            wc_result = await _run_cmd(
                ["sh", "-c", f"wc -l < {_shell_quote(filepath)} 2>/dev/null"],
                timeout=3,
            )
            if wc_result["exit_code"] == 0:
                try:
                    entry["total_lines"] = int(wc_result["stdout"].strip())
                except ValueError:
                    pass
        else:
            entry["error"] = result["stderr"] or result["stdout"]
        results.append(entry)

    return {"files": results}
