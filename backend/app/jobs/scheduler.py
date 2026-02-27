"""Extensible async job scheduler for recurring background tasks."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Awaitable

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

logger = logging.getLogger(__name__)

# Job function signature: async fn(session_factory) -> None
JobFn = Callable[[async_sessionmaker[AsyncSession]], Awaitable[None]]


@dataclass
class _JobEntry:
    name: str
    fn: JobFn
    interval_seconds: int
    last_run: float = 0.0
    running: bool = False


class JobScheduler:
    """Simple async job scheduler that runs registered jobs on intervals.

    Usage:
        scheduler = JobScheduler(session_factory)
        scheduler.register("sync_models", sync_models_fn, interval_seconds=21600)
        await scheduler.start()
        # ... later ...
        await scheduler.stop()
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._jobs: dict[str, _JobEntry] = {}
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    def register(self, name: str, fn: JobFn, interval_seconds: int) -> None:
        """Register a recurring job."""
        self._jobs[name] = _JobEntry(
            name=name, fn=fn, interval_seconds=interval_seconds
        )
        logger.info("Registered job %r (every %ds)", name, interval_seconds)

    async def trigger(self, name: str) -> None:
        """Run a job immediately (on-demand). Safe to call from request handlers."""
        entry = self._jobs.get(name)
        if not entry:
            logger.warning("trigger: unknown job %r", name)
            return
        if entry.running:
            logger.info("trigger: job %r already running, skipping", name)
            return
        await self._run_job(entry)

    async def start(self) -> None:
        """Start the scheduler loop. Runs all jobs immediately on first tick."""
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name="job-scheduler")
        logger.info("Job scheduler started with %d job(s)", len(self._jobs))

    async def stop(self) -> None:
        """Stop the scheduler gracefully."""
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Job scheduler stopped")

    async def _loop(self) -> None:
        """Main scheduler loop — checks every 30s for due jobs."""
        # Run all jobs immediately on startup
        for entry in self._jobs.values():
            if not self._stop_event.is_set():
                await self._run_job(entry)

        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=30.0)
                break  # stop event was set
            except asyncio.TimeoutError:
                pass  # 30s tick — check for due jobs

            now = time.monotonic()
            for entry in self._jobs.values():
                if (
                    not entry.running
                    and (now - entry.last_run) >= entry.interval_seconds
                ):
                    await self._run_job(entry)

    async def _run_job(self, entry: _JobEntry) -> None:
        """Execute a single job with error handling."""
        entry.running = True
        start = time.monotonic()
        try:
            logger.info("Running job %r", entry.name)
            await entry.fn(self._session_factory)
            elapsed = time.monotonic() - start
            logger.info("Job %r completed in %.1fs", entry.name, elapsed)
        except Exception:
            elapsed = time.monotonic() - start
            logger.exception("Job %r failed after %.1fs", entry.name, elapsed)
        finally:
            entry.last_run = time.monotonic()
            entry.running = False
