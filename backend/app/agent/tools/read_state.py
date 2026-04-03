"""
mtime-based file read dedup for Phase 3 of Design Doc 098.

Tracks file modification times across tool calls within a conversation.
When a file is re-read with the same line range and hasn't changed (same mtime),
returns a compact "unchanged" stub instead of the full content — saving tokens.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ReadRecord:
    """A record of a previous file read."""

    mtime: float
    line_start: int | None
    line_end: int | None
    token_count: int


# ---------------------------------------------------------------------------
# Token estimation helpers
# ---------------------------------------------------------------------------

# Two-phase budget constants
MAX_PRE_READ_BYTES: int = 256_000  # Phase 1: cheap stat() gate
MAX_POST_READ_TOKENS: int = 25_000  # Phase 2: token gate after read


def estimate_tokens(text: str) -> int:
    """Rough token estimate — ~4 chars per token for code."""
    return len(text) // 4


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate *text* to approximately *max_tokens* on a line boundary."""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_newline = truncated.rfind("\n")
    # Don't lose too much by snapping to a line boundary
    if last_newline > max_chars * 0.8:
        truncated = truncated[: last_newline + 1]
    return truncated


# ---------------------------------------------------------------------------
# ReadState — per-conversation singleton
# ---------------------------------------------------------------------------


class ReadState:
    """Tracks file read mtime for dedup within a conversation."""

    def __init__(self) -> None:
        self._records: dict[str, ReadRecord] = {}

    # -- query / mutate -----------------------------------------------------

    def check(
        self,
        path: str,
        mtime: float,
        line_start: int | None,
        line_end: int | None,
    ) -> ReadRecord | None:
        """Return the previous `ReadRecord` if the file is unchanged, else *None*."""
        prev = self._records.get(path)
        if (
            prev
            and prev.mtime == mtime
            and prev.line_start == line_start
            and prev.line_end == line_end
        ):
            return prev
        return None

    def record(
        self,
        path: str,
        mtime: float,
        line_start: int | None,
        line_end: int | None,
        token_count: int,
    ) -> None:
        """Store a read record for *path*."""
        self._records[path] = ReadRecord(
            mtime=mtime,
            line_start=line_start,
            line_end=line_end,
            token_count=token_count,
        )

    def invalidate(self, path: str) -> None:
        """Remove the record for *path* (e.g. after a write)."""
        self._records.pop(path, None)

    def reset(self) -> None:
        """Clear all records (e.g. at conversation start)."""
        self._records.clear()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_read_state = ReadState()


def get_read_state() -> ReadState:
    """Return the module-level ReadState singleton."""
    return _read_state


def reset_read_state() -> None:
    """Reset the module-level ReadState singleton."""
    _read_state.reset()
