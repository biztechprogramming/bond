"""Workspace cloning for concurrent container isolation (Design Doc 057).

Detects git layout of workspace directories and clones them into per-agent
copies so multiple containers can operate on independent working trees.

Clones are stored outside the project tree (default: ~/.bond/workspaces/)
to avoid triggering dev-server file watchers. Configurable via
BOND_WORKSPACE_CLONE_DIR environment variable.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("bond.sandbox.workspace_cloner")


def _get_clone_root() -> Path:
    """Return the root directory for workspace clones.

    Defaults to ~/.bond/workspaces. Override with BOND_WORKSPACE_CLONE_DIR.
    """
    env_override = os.environ.get("BOND_WORKSPACE_CLONE_DIR")
    if env_override:
        return Path(env_override).expanduser().resolve()
    return Path.home() / ".bond" / "workspaces"

# Build artifacts to skip during copy operations
_SKIP_PATTERNS: list[str] = [
    "dist",
    ".next",
    "__pycache__",
    ".cache",
    "*.pyc",
    ".turbo",
    ".parcel-cache",
    "node_modules",
]

# Lockfile -> install command mapping
_LOCKFILE_COMMANDS: list[tuple[str, str]] = [
    ("bun.lock", "bun install"),
    ("bun.lockb", "bun install"),
    ("package-lock.json", "npm ci"),
    ("yarn.lock", "yarn install --frozen-lockfile"),
    ("pnpm-lock.yaml", "pnpm install --frozen-lockfile"),
    ("requirements.txt", "pip install -r requirements.txt"),
    ("Pipfile.lock", "pipenv install"),
    ("go.sum", "go mod download"),
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class RepoCloneSpec:
    repo_root: str       # host path to repo root
    remote: str          # clone source (file:// URL)
    branch: str          # branch to check out
    target_path: str     # host path for clone destination


@dataclass
class CopySpec:
    source: str
    target: str


@dataclass
class ClonePlan:
    case: int            # 1 or 3 (cases 2 and 4 resolve to case 1 or direct mount)
    repos: list[RepoCloneSpec] = field(default_factory=list)
    copies: list[CopySpec] = field(default_factory=list)
    direct_mount: bool = False  # true if user declined — no clone, no concurrency
    clone_base: str = ""  # root of the cloned workspace on host


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


async def detect_workspace_type(host_path: str) -> dict:
    """Classify a workspace directory into one of four cases.

    Returns:
        {
            "case": int,          # 1-4
            "repo_root": str | None,
            "sub_repos": list[str],
            "needs_prompt": bool,
            "prompt_message": str,
        }
    """
    p = Path(host_path).resolve()

    # Case 1: directory IS a git repo root
    if (p / ".git").exists():
        return {
            "case": 1,
            "repo_root": str(p),
            "sub_repos": [],
            "needs_prompt": False,
            "prompt_message": "",
        }

    # Case 2: directory is INSIDE a git repo (walk up)
    ancestor = p.parent
    while ancestor != ancestor.parent:
        if (ancestor / ".git").exists():
            return {
                "case": 2,
                "repo_root": str(ancestor),
                "sub_repos": [],
                "needs_prompt": True,
                "prompt_message": (
                    f"This directory is inside a git repo rooted at "
                    f"`{ancestor}`. Do you want to mount the repo root instead?"
                ),
            }
        ancestor = ancestor.parent

    # Case 3: directory CONTAINS git repos (scan up to 3 levels deep)
    sub_repos = _scan_for_repos(p, max_depth=3)
    if sub_repos:
        return {
            "case": 3,
            "repo_root": None,
            "sub_repos": [str(r) for r in sub_repos],
            "needs_prompt": False,
            "prompt_message": "",
        }

    # Case 4: no git at all
    return {
        "case": 4,
        "repo_root": None,
        "sub_repos": [],
        "needs_prompt": True,
        "prompt_message": (
            "This directory is not a git repo. Would you like to "
            "initialize one so it can support concurrent containers?"
        ),
    }


def _scan_for_repos(root: Path, max_depth: int) -> list[Path]:
    """Find directories containing .git/ within *max_depth* levels of *root*."""
    repos: list[Path] = []
    _scan_recursive(root, root, max_depth, repos)
    return repos


def _scan_recursive(
    base: Path, current: Path, remaining_depth: int, found: list[Path],
) -> None:
    if remaining_depth <= 0:
        return
    try:
        entries = sorted(current.iterdir())
    except PermissionError:
        return
    for entry in entries:
        if not entry.is_dir():
            continue
        if entry.name == ".git":
            found.append(current)
            return  # don't recurse into this repo's children
        if entry.name.startswith("."):
            continue
        _scan_recursive(base, entry, remaining_depth - 1, found)


# ---------------------------------------------------------------------------
# Plan generation
# ---------------------------------------------------------------------------


def _is_ancestor_of_any(directory: Path, repo_roots: set[Path]) -> bool:
    """Check if *directory* is a proper ancestor of any repo root."""
    for repo in repo_roots:
        try:
            repo.relative_to(directory)
            if repo != directory:
                return True
        except ValueError:
            continue
    return False


def _collect_non_repo_copies(
    source_dir: Path,
    target_dir: Path,
    repo_roots: set[Path],
    copies: list[CopySpec],
) -> None:
    """Recursively collect CopySpecs for non-repo content.

    - Entries that ARE repo roots are skipped (handled by git clone).
    - Entries that are ancestors of repo roots are recursed into —
      their non-repo children get copied, but the directory itself isn't
      copied wholesale.
    - Everything else (files, unrelated dirs) is copied as-is.
    """
    try:
        entries = sorted(source_dir.iterdir())
    except PermissionError:
        return

    for entry in entries:
        if entry.name.startswith("."):
            continue

        if entry in repo_roots:
            # This is a git repo — will be cloned, skip it
            continue

        if entry.is_dir() and _is_ancestor_of_any(entry, repo_roots):
            # This directory contains repo(s) deeper down — recurse
            # but don't copy it wholesale
            _collect_non_repo_copies(
                entry, target_dir / entry.name, repo_roots, copies,
            )
        else:
            # Regular file or directory with no repos inside — copy it
            copies.append(CopySpec(
                source=str(entry),
                target=str(target_dir / entry.name),
            ))


async def generate_clone_plan(
    host_path: str,
    agent_id: str,
    mount_name: str,
    detection: dict | None = None,
) -> ClonePlan:
    """Build a ClonePlan for the given workspace mount.

    For cases 2 and 4 (needs_prompt=True), returns a plan with
    direct_mount=True since we can't prompt the user yet.
    """
    if detection is None:
        detection = await detect_workspace_type(host_path)

    case = detection["case"]
    clone_base = _get_clone_root() / agent_id / mount_name

    if case == 1:
        repo_root = detection["repo_root"]
        branch = await _get_current_branch(repo_root)
        return ClonePlan(
            case=1,
            repos=[RepoCloneSpec(
                repo_root=repo_root,
                remote=f"file://{repo_root}",
                branch=branch,
                target_path=str(clone_base),
            )],
            clone_base=str(clone_base),
        )

    if case == 3:
        repos = []
        copies = []
        p = Path(host_path).resolve()
        sub_repos = detection["sub_repos"]

        for repo_path in sub_repos:
            rel = Path(repo_path).relative_to(p)
            target = clone_base / rel
            branch = await _get_current_branch(repo_path)
            repos.append(RepoCloneSpec(
                repo_root=repo_path,
                remote=f"file://{repo_path}",
                branch=branch,
                target_path=str(target),
            ))

        # Find non-repo files/dirs to copy.
        # We need to handle intermediate directories that *contain* repos
        # deeper down — we can't copy them wholesale (that would duplicate
        # the repo contents). Instead, walk into them and only copy the
        # non-repo entries at each level.
        repo_root_set = {Path(r) for r in sub_repos}
        _collect_non_repo_copies(p, clone_base, repo_root_set, copies)

        return ClonePlan(case=3, repos=repos, copies=copies, clone_base=str(clone_base))

    # Cases 2 and 4: needs user prompt — direct mount for now
    return ClonePlan(case=case, direct_mount=True)


async def _get_current_branch(repo_path: str) -> str:
    """Get the current branch of a git repo."""
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", repo_path, "rev-parse", "--abbrev-ref", "HEAD",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    branch = stdout.decode().strip()
    return branch if branch and branch != "HEAD" else "main"


# ---------------------------------------------------------------------------
# Clone execution
# ---------------------------------------------------------------------------


async def _refresh_clone(repo: RepoCloneSpec) -> bool:
    """Try to refresh an existing clone via fetch+reset. Returns True if successful."""
    target = Path(repo.target_path)
    if not (target / ".git").exists():
        return False

    try:
        fetch_proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(target), "fetch", "--depth", "1", "origin", repo.branch,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await fetch_proc.communicate()
        if fetch_proc.returncode != 0:
            logger.warning("Refresh fetch failed for %s: %s", repo.target_path, stderr.decode())
            return False

        reset_proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(target), "reset", "--hard", f"origin/{repo.branch}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await reset_proc.communicate()
        if reset_proc.returncode != 0:
            logger.warning("Refresh reset failed for %s: %s", repo.target_path, stderr.decode())
            return False

        logger.info("Refreshed clone %s (branch=%s)", repo.target_path, repo.branch)
        return True
    except Exception as e:
        logger.warning("Refresh failed for %s: %s", repo.target_path, e)
        return False


async def _clone_repo(repo: RepoCloneSpec) -> None:
    """Clone a single repo, using refresh if a valid clone exists."""
    target = Path(repo.target_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Try refreshing existing clone first
    if await _refresh_clone(repo):
        return

    # Full clone: remove stale target if present
    if target.exists():
        shutil.rmtree(str(target))

    proc = await asyncio.create_subprocess_exec(
        "git", "clone", "--depth", "1",
        "--branch", repo.branch,
        repo.remote, str(target),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"git clone failed for {repo.remote}: {stderr.decode()}"
        )
    logger.info(
        "Cloned %s (branch=%s) -> %s",
        repo.repo_root, repo.branch, repo.target_path,
    )


async def execute_clone_plan(plan: ClonePlan) -> None:
    """Execute a clone plan: git clone repos, copy non-repo files."""
    if plan.direct_mount:
        return

    # Clone all repos in parallel
    if plan.repos:
        await asyncio.gather(*[_clone_repo(repo) for repo in plan.repos])

    # Load .cloneignore patterns if present
    ignore_patterns = _SKIP_PATTERNS[:]
    for copy_spec in plan.copies:
        source_parent = Path(copy_spec.source).parent
        cloneignore = source_parent / ".cloneignore"
        if cloneignore.exists():
            ignore_patterns.extend(_load_cloneignore(cloneignore))
            break

    for copy_spec in plan.copies:
        source = Path(copy_spec.source)
        target = Path(copy_spec.target)

        if _should_skip(source.name, ignore_patterns):
            continue

        # Skip copy if target is up-to-date (mtime check)
        if target.exists() and source.exists():
            try:
                if source.stat().st_mtime <= target.stat().st_mtime:
                    continue
            except OSError:
                pass

        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            _copytree_filtered(source, target, ignore_patterns)
        else:
            shutil.copy2(str(source), str(target))

    logger.info("Clone plan executed: %d repos, %d copies", len(plan.repos), len(plan.copies))


def _load_cloneignore(path: Path) -> list[str]:
    """Load gitignore-style patterns from a .cloneignore file."""
    patterns: list[str] = []
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                patterns.append(line)
    except OSError:
        pass
    return patterns


def _should_skip(name: str, patterns: list[str]) -> bool:
    """Check if a file/directory name matches any skip pattern."""
    for pattern in patterns:
        if fnmatch.fnmatch(name, pattern):
            return True
    return False


def _copytree_filtered(source: Path, target: Path, patterns: list[str]) -> None:
    """Copy a directory tree, skipping entries matching patterns."""
    target.mkdir(parents=True, exist_ok=True)
    for entry in source.iterdir():
        if _should_skip(entry.name, patterns):
            continue
        dest = target / entry.name
        if entry.is_dir():
            _copytree_filtered(entry, dest, patterns)
        else:
            shutil.copy2(str(entry), str(dest))


# ---------------------------------------------------------------------------
# Post-clone env handling
# ---------------------------------------------------------------------------


async def copy_env_files(
    source_dir: str, target_dir: str, instance_index: int,
) -> None:
    """Copy .env* files from source to target, applying per-instance overrides."""
    src = Path(source_dir)
    tgt = Path(target_dir)

    for entry in src.iterdir():
        if entry.is_file() and entry.name.startswith(".env"):
            content = entry.read_text()
            content = _apply_instance_overrides(content, instance_index)
            dest = tgt / entry.name
            dest.write_text(content)
            logger.debug("Copied env file %s -> %s", entry, dest)

    # Inject CONTAINER_INSTANCE_ID
    env_local = tgt / ".env.local"
    extra = f"\n# Auto-injected by workspace cloner\nCONTAINER_INSTANCE_ID={instance_index}\n"
    if env_local.exists():
        with open(env_local, "a") as f:
            f.write(extra)
    else:
        env_local.write_text(extra)


def _apply_instance_overrides(content: str, instance_index: int) -> str:
    """Increment port numbers and append instance suffixes to paths."""
    lines = content.splitlines()
    result = []
    for line in lines:
        if "=" not in line or line.strip().startswith("#"):
            result.append(line)
            continue

        key, _, value = line.partition("=")
        key_upper = key.strip().upper()

        # Port increment: any key containing PORT
        if "PORT" in key_upper:
            try:
                port = int(value.strip())
                value = str(port + instance_index)
            except ValueError:
                pass

        # Database path suffix: any key containing DB_PATH or DATABASE
        if "DB_PATH" in key_upper or "DATABASE" in key_upper:
            value = value.strip()
            if value and not value.endswith("/"):
                base, ext = os.path.splitext(value)
                if ext:
                    value = f"{base}_instance{instance_index}{ext}"
                else:
                    value = f"{value}_instance{instance_index}"

        result.append(f"{key}={value}")

    return "\n".join(result)


# ---------------------------------------------------------------------------
# Lockfile detection
# ---------------------------------------------------------------------------


def detect_lockfiles(directory: str) -> list[tuple[str, str]]:
    """Detect lockfiles and return (lockfile_path, install_command) tuples."""
    found: list[tuple[str, str]] = []
    d = Path(directory)

    # Check for pyproject.toml + uv.lock special case
    if (d / "pyproject.toml").exists() and (d / "uv.lock").exists():
        found.append((str(d / "uv.lock"), "uv sync"))

    for lockfile, cmd in _LOCKFILE_COMMANDS:
        path = d / lockfile
        if path.exists():
            found.append((str(path), cmd))

    return found


# ---------------------------------------------------------------------------
# Dependency install script generation
# ---------------------------------------------------------------------------


def generate_dep_install_script(clone_path: str) -> str | None:
    """Generate a shell script that installs all detected deps, or None if none found."""
    lockfiles = detect_lockfiles(clone_path)
    if not lockfiles:
        return None

    lines = ["#!/bin/sh", "set -e", f"cd {clone_path}"]
    for _lockfile_path, install_cmd in lockfiles:
        lines.append(install_cmd)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


async def cleanup_workspace_clones(agent_id: str) -> None:
    """Delete cloned workspaces for an agent."""
    workspaces_dir = _get_clone_root() / agent_id
    if workspaces_dir.exists():
        shutil.rmtree(str(workspaces_dir))
        logger.info("Cleaned up workspace clones for agent %s", agent_id)
