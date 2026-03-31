"""Content-hash caching for repo maps.

Caches rendered map text keyed by a SHA-256 hash of file metadata + token budget.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

logger = logging.getLogger("bond.agent.repomap.cache")


class RepoMapCache:
    """Cache repo maps by content hash."""

    def __init__(self, cache_dir: str | Path | None = None):
        if cache_dir is None:
            bond_home = os.environ.get("BOND_HOME", "")
            if bond_home:
                self.cache_dir = Path(bond_home) / "cache" / "repomap"
            else:
                self.cache_dir = Path("data/repomap-cache")
        else:
            self.cache_dir = Path(cache_dir)

    def _repo_hash(self, repo_root: str, files: list[str], budget: int) -> str:
        """Compute a hash representing the current state of all files + budget."""
        hasher = hashlib.sha256()
        for filepath in sorted(files):
            full_path = Path(repo_root) / filepath
            try:
                stat = full_path.stat()
                hasher.update(f"{filepath}:{stat.st_mtime}:{stat.st_size}".encode())
            except OSError:
                continue
        hasher.update(f"budget:{budget}".encode())
        return hasher.hexdigest()

    def get(self, repo_root: str, files: list[str], budget: int) -> str | None:
        """Return cached map if repo state hasn't changed."""
        repo_hash = self._repo_hash(repo_root, files, budget)
        cache_file = self.cache_dir / f"{repo_hash}.txt"
        if cache_file.exists():
            try:
                return cache_file.read_text()
            except OSError:
                return None
        return None

    def set(self, repo_root: str, files: list[str], budget: int, content: str) -> None:
        """Cache the generated map."""
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            repo_hash = self._repo_hash(repo_root, files, budget)
            cache_file = self.cache_dir / f"{repo_hash}.txt"
            cache_file.write_text(content)
            self._evict_old()
        except OSError as e:
            logger.warning("Failed to cache repo map: %s", e)

    def _evict_old(self, max_entries: int = 20) -> None:
        """Remove oldest cache entries if over limit."""
        try:
            entries = sorted(self.cache_dir.glob("*.txt"), key=lambda p: p.stat().st_mtime)
            for entry in entries[:-max_entries]:
                entry.unlink(missing_ok=True)
        except OSError:
            pass
