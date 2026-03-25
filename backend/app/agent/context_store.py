"""FTS5 knowledge base for automatic context indexing (Design Doc 075).

Per-conversation SQLite database with FTS5 full-text search. Stores chunked
tool outputs so the agent can search them via ctx_search after context decay
prunes the original content.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_CHUNK_SIZE = 4096  # bytes
DATA_DIR = os.environ.get("BOND_DATA_DIR", "data")
INDEX_DIR = os.path.join(DATA_DIR, "context_index")

# ---------------------------------------------------------------------------
# Log detection
# ---------------------------------------------------------------------------

_LOG_PATTERNS = [
    re.compile(r"^\d{4}[-/]\d{2}[-/]\d{2}"),           # ISO date prefix
    re.compile(r"^\[?\d{2}:\d{2}:\d{2}"),               # Time prefix
    re.compile(r"^\[?(INFO|WARN|ERROR|DEBUG|TRACE)\b"),  # Log level prefix
    re.compile(r"^\w{3}\s+\d{1,2}\s+\d{2}:\d{2}"),     # Syslog format
]

_LOG_LEVEL_RE = re.compile(r"\b(ERROR|WARN(?:ING)?|INFO|DEBUG|TRACE)\b", re.IGNORECASE)


def is_log_shaped(content: str, sample_size: int = 20) -> bool:
    """Check if content looks like log output."""
    lines = content.strip().splitlines()[:sample_size]
    if len(lines) < 5:
        return False
    matches = sum(
        1 for line in lines
        if any(p.match(line.strip()) for p in _LOG_PATTERNS)
    )
    return matches / len(lines) > 0.6


# ---------------------------------------------------------------------------
# Content shape detection & chunking
# ---------------------------------------------------------------------------

def _detect_content_shape(content: str, tool_name: str = "") -> str:
    """Detect content shape: 'log', 'code', 'json', 'plain'."""
    if is_log_shaped(content):
        return "log"
    if tool_name in ("file_read", "file_open", "file_view"):
        return "code"
    stripped = content.strip()
    if stripped.startswith(("{", "[")) and (stripped.endswith("}") or stripped.endswith("]")):
        try:
            json.loads(stripped)
            return "json"
        except (json.JSONDecodeError, ValueError):
            pass
    return "plain"


def _chunk_plain(content: str, title_prefix: str) -> list[dict[str, str]]:
    """Chunk plain text into 20-line chunks with 2-line overlap."""
    lines = content.splitlines()
    chunks = []
    chunk_size = 20
    overlap = 2
    i = 0
    chunk_num = 0
    while i < len(lines):
        end = min(i + chunk_size, len(lines))
        chunk_lines = lines[i:end]
        chunk_text = "\n".join(chunk_lines)
        if len(chunk_text.encode("utf-8")) > MAX_CHUNK_SIZE:
            chunk_text = chunk_text[:MAX_CHUNK_SIZE].rsplit("\n", 1)[0]
        chunk_num += 1
        chunks.append({
            "title": f"{title_prefix} — chunk {chunk_num}",
            "content": chunk_text,
        })
        i = end - overlap if end < len(lines) else end
    return chunks or [{"title": title_prefix, "content": content[:MAX_CHUNK_SIZE]}]


def _chunk_log(content: str, title_prefix: str) -> list[dict[str, str]]:
    """Log-aware chunking: group by level, deduplicate INFO, keep ERROR/WARN."""
    lines = content.splitlines()
    by_level: dict[str, list[str]] = {"ERROR": [], "WARN": [], "INFO": [], "DEBUG": [], "OTHER": []}

    for line in lines:
        m = _LOG_LEVEL_RE.search(line)
        if m:
            level = m.group(1).upper()
            if level == "WARNING":
                level = "WARN"
            if level in by_level:
                by_level[level].append(line)
            else:
                by_level["OTHER"].append(line)
        else:
            by_level["OTHER"].append(line)

    chunks = []

    # ERROR lines — keep all verbatim
    if by_level["ERROR"]:
        error_text = "\n".join(by_level["ERROR"])
        for i in range(0, len(error_text), MAX_CHUNK_SIZE):
            chunks.append({
                "title": f"{title_prefix} — ERROR lines",
                "content": error_text[i:i + MAX_CHUNK_SIZE],
            })

    # WARN lines — keep all verbatim
    if by_level["WARN"]:
        warn_text = "\n".join(by_level["WARN"])
        for i in range(0, len(warn_text), MAX_CHUNK_SIZE):
            chunks.append({
                "title": f"{title_prefix} — WARN lines",
                "content": warn_text[i:i + MAX_CHUNK_SIZE],
            })

    # INFO — deduplicate
    if by_level["INFO"]:
        from collections import Counter
        # Strip timestamps for dedup
        templates = Counter()
        for line in by_level["INFO"]:
            # Remove leading timestamp
            cleaned = re.sub(r"^\S+\s+", "", line)
            templates[cleaned] += 1
        deduped_lines = []
        for template, count in templates.most_common():
            if count > 5:
                deduped_lines.append(f"[repeated {count} times] {template}")
            else:
                deduped_lines.append(template)
        info_text = "\n".join(deduped_lines)
        if info_text:
            chunks.append({
                "title": f"{title_prefix} — INFO summary",
                "content": info_text[:MAX_CHUNK_SIZE],
            })

    # Time range
    timestamps = []
    for line in lines[:5] + lines[-5:]:
        ts_match = re.match(r"(\d{4}[-/]\d{2}[-/]\d{2}[\sT]\d{2}:\d{2}:\d{2})", line)
        if ts_match:
            timestamps.append(ts_match.group(1))
    if timestamps:
        chunks.append({
            "title": f"{title_prefix} — time range",
            "content": f"First: {timestamps[0]}\nLast: {timestamps[-1]}\nTotal lines: {len(lines)}",
        })

    return chunks or _chunk_plain(content, title_prefix)


def _chunk_code(content: str, title_prefix: str) -> list[dict[str, str]]:
    """Chunk code by blank-line boundaries, preserving line numbers."""
    lines = content.splitlines()
    chunks = []
    current_block: list[str] = []
    current_start = 1

    for i, line in enumerate(lines, 1):
        current_block.append(f"{i}: {line}")
        block_text = "\n".join(current_block)
        if len(block_text.encode("utf-8")) >= MAX_CHUNK_SIZE or (
            line.strip() == "" and len(current_block) > 10
        ):
            chunks.append({
                "title": f"{title_prefix} — lines {current_start}-{i}",
                "content": "\n".join(current_block),
            })
            current_block = []
            current_start = i + 1

    if current_block:
        end_line = current_start + len(current_block) - 1
        chunks.append({
            "title": f"{title_prefix} — lines {current_start}-{end_line}",
            "content": "\n".join(current_block),
        })

    return chunks or [{"title": title_prefix, "content": content[:MAX_CHUNK_SIZE]}]


def _chunk_json(content: str, title_prefix: str) -> list[dict[str, str]]:
    """Chunk JSON by top-level keys."""
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return _chunk_plain(content, title_prefix)

    if isinstance(data, dict):
        chunks = []
        for key, value in data.items():
            serialized = json.dumps({key: value}, indent=2)
            if len(serialized.encode("utf-8")) > MAX_CHUNK_SIZE:
                serialized = serialized[:MAX_CHUNK_SIZE]
            chunks.append({
                "title": f"{title_prefix} — {key}",
                "content": serialized,
            })
        return chunks or [{"title": title_prefix, "content": content[:MAX_CHUNK_SIZE]}]

    # Array: chunk by rows
    if isinstance(data, list):
        chunks = []
        batch: list[Any] = []
        batch_size = 0
        batch_num = 0
        for item in data:
            item_str = json.dumps(item)
            if batch_size + len(item_str) > MAX_CHUNK_SIZE and batch:
                batch_num += 1
                chunks.append({
                    "title": f"{title_prefix} — items batch {batch_num}",
                    "content": json.dumps(batch, indent=2),
                })
                batch = []
                batch_size = 0
            batch.append(item)
            batch_size += len(item_str)
        if batch:
            batch_num += 1
            chunks.append({
                "title": f"{title_prefix} — items batch {batch_num}",
                "content": json.dumps(batch, indent=2),
            })
        return chunks or [{"title": title_prefix, "content": content[:MAX_CHUNK_SIZE]}]

    return _chunk_plain(content, title_prefix)


def chunk_content(content: str, tool_name: str = "", tool_args: dict | None = None) -> list[dict[str, str]]:
    """Chunk content based on detected shape. Returns list of {title, content}."""
    args_summary = ""
    if tool_args:
        for key in ("path", "file_path", "code", "command", "pattern", "query"):
            if key in tool_args:
                val = str(tool_args[key])[:80]
                args_summary = val
                break
    title_prefix = f"{tool_name}({args_summary})" if args_summary else tool_name or "output"

    shape = _detect_content_shape(content, tool_name)

    if shape == "log":
        return _chunk_log(content, title_prefix)
    elif shape == "code":
        return _chunk_code(content, title_prefix)
    elif shape == "json":
        return _chunk_json(content, title_prefix)
    else:
        return _chunk_plain(content, title_prefix)


# ---------------------------------------------------------------------------
# Fuzzy correction (Levenshtein)
# ---------------------------------------------------------------------------

def _levenshtein(s1: str, s2: str) -> int:
    """Compute Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (c1 != c2)))
        prev = curr
    return prev[-1]


# ---------------------------------------------------------------------------
# ContextStore — per-conversation FTS5 database
# ---------------------------------------------------------------------------

class ContextStore:
    """FTS5-backed context index for a single conversation."""

    def __init__(self, conversation_id: str):
        self.conversation_id = conversation_id
        self._db: sqlite3.Connection | None = None

    @property
    def db_path(self) -> str:
        return os.path.join(INDEX_DIR, f"{self.conversation_id}.db")

    def _ensure_db(self) -> sqlite3.Connection:
        """Lazily create/open the database."""
        if self._db is not None:
            return self._db

        os.makedirs(INDEX_DIR, exist_ok=True)
        self._db = sqlite3.connect(self.db_path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")

        self._db.executescript("""
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
                title,
                content,
                source_id UNINDEXED,
                content_type UNINDEXED,
                tokenize = 'porter unicode61'
            );

            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_name TEXT NOT NULL,
                tool_args TEXT,
                turn_number INTEGER,
                original_bytes INTEGER,
                chunk_count INTEGER DEFAULT 0,
                indexed_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS vocabulary (
                word TEXT PRIMARY KEY
            );
        """)
        self._db.commit()
        return self._db

    def index(
        self,
        content: str,
        tool_name: str,
        tool_args: dict | None = None,
        turn_number: int = 0,
        content_type: str = "tool_output",
    ) -> int:
        """Index content into FTS5. Returns source_id."""
        db = self._ensure_db()
        original_bytes = len(content.encode("utf-8"))

        cursor = db.execute(
            "INSERT INTO sources (tool_name, tool_args, turn_number, original_bytes) VALUES (?, ?, ?, ?)",
            (tool_name, json.dumps(tool_args) if tool_args else None, turn_number, original_bytes),
        )
        source_id = cursor.lastrowid

        chunks = chunk_content(content, tool_name, tool_args)

        for chunk in chunks:
            db.execute(
                "INSERT INTO chunks (title, content, source_id, content_type) VALUES (?, ?, ?, ?)",
                (chunk["title"], chunk["content"], str(source_id), content_type),
            )

        # Update chunk count
        db.execute("UPDATE sources SET chunk_count = ? WHERE id = ?", (len(chunks), source_id))

        # Update vocabulary
        words = set()
        for chunk in chunks:
            for word in re.findall(r"\w{3,}", chunk["content"]):
                words.add(word.lower())
        if words:
            db.executemany(
                "INSERT OR IGNORE INTO vocabulary (word) VALUES (?)",
                [(w,) for w in words],
            )

        db.commit()
        logger.info(
            "Indexed %s: %d bytes → %d chunks (source_id=%d)",
            tool_name, original_bytes, len(chunks), source_id,
        )
        return source_id

    def search(self, queries: list[str], limit: int = 5) -> list[dict[str, Any]]:
        """Search indexed content. Returns list of result dicts."""
        db = self._ensure_db()
        results: list[dict[str, Any]] = []
        seen_rowids: set[int] = set()

        for query in queries:
            # Escape FTS5 special characters
            safe_query = re.sub(r'[^\w\s]', ' ', query).strip()
            if not safe_query:
                continue

            # Porter BM25 search
            try:
                rows = db.execute(
                    """
                    SELECT rowid, title, content, source_id,
                           bm25(chunks, 5.0, 1.0) AS rank
                    FROM chunks
                    WHERE chunks MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (safe_query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []

            if not rows:
                # Fuzzy correction: find closest vocabulary word
                corrected = self._fuzzy_correct(safe_query, max_distance=2)
                if corrected and corrected != safe_query:
                    try:
                        rows = db.execute(
                            """
                            SELECT rowid, title, content, source_id,
                                   bm25(chunks, 5.0, 1.0) AS rank
                            FROM chunks
                            WHERE chunks MATCH ?
                            ORDER BY rank
                            LIMIT ?
                            """,
                            (corrected, limit),
                        ).fetchall()
                    except sqlite3.OperationalError:
                        rows = []

            for row in rows:
                rowid, title, content, source_id, rank = row
                if rowid in seen_rowids:
                    continue
                seen_rowids.add(rowid)

                # Get source info
                source = db.execute(
                    "SELECT tool_name, turn_number FROM sources WHERE id = ?",
                    (int(source_id),),
                ).fetchone()

                snippet = content[:1024] if len(content) > 1024 else content

                results.append({
                    "query": query,
                    "title": title,
                    "content": snippet,
                    "source_tool": source[0] if source else "unknown",
                    "turn_number": source[1] if source else 0,
                    "rank": rank,
                })

        return results[:limit]

    def _fuzzy_correct(self, query: str, max_distance: int = 2) -> str | None:
        """Find closest vocabulary word for fuzzy correction."""
        db = self._ensure_db()
        words = query.lower().split()
        corrected = []
        changed = False

        vocab = [r[0] for r in db.execute("SELECT word FROM vocabulary").fetchall()]
        if not vocab:
            return None

        for word in words:
            if len(word) < 3:
                corrected.append(word)
                continue
            # Check if word exists in vocabulary
            exists = db.execute("SELECT 1 FROM vocabulary WHERE word = ?", (word,)).fetchone()
            if exists:
                corrected.append(word)
                continue
            # Find closest match
            best_word = word
            best_dist = max_distance + 1
            for v in vocab:
                if abs(len(v) - len(word)) > max_distance:
                    continue
                dist = _levenshtein(word, v)
                if dist < best_dist:
                    best_dist = dist
                    best_word = v
            if best_dist <= max_distance:
                corrected.append(best_word)
                changed = True
            else:
                corrected.append(word)

        return " ".join(corrected) if changed else None

    def get_stats(self) -> dict[str, int]:
        """Get index statistics."""
        db = self._ensure_db()
        source_count = db.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        chunk_count = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return {"sources": source_count, "chunks": chunk_count}

    def close(self):
        """Close the database connection."""
        if self._db:
            try:
                self._db.close()
            except Exception:
                pass  # May fail if created in a different thread (background indexing)
            self._db = None

    @staticmethod
    def delete(conversation_id: str):
        """Delete the index database for a conversation."""
        db_path = os.path.join(INDEX_DIR, f"{conversation_id}.db")
        for suffix in ("", "-wal", "-shm"):
            path = db_path + suffix
            if os.path.exists(path):
                os.remove(path)
        logger.info("Deleted context index for conversation %s", conversation_id)
