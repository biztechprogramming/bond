"""Task manager — a simple in-memory task tracker.

Supports creating, listing, completing, and deleting tasks
with priority levels and tags.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Priority(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Task:
    id: int
    title: str
    description: str = ""
    priority: Priority = Priority.MEDIUM
    tags: list[str] = field(default_factory=list)
    completed: bool = False
    created_at: str = ""
    completed_at: str | None = None

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


class TaskManager:
    """Manages a collection of tasks."""

    def __init__(self) -> None:
        self._tasks: dict[int, Task] = {}
        self._next_id: int = 1

    def add(self, title: str, description: str = "", priority: str = "medium",
            tags: list[str] | None = None) -> Task:
        """Create a new task and return it."""
        task = Task(
            id=self._next_id,
            title=title,
            description=description,
            priority=Priority(priority),
            tags=tags or [],
        )
        self._tasks[task.id] = task
        self._next_id += 1
        return task

    def get(self, task_id: int) -> Task | None:
        """Get a task by ID."""
        return self._tasks.get(task_id)

    def list_all(self) -> list[Task]:
        """Return all tasks, ordered by ID."""
        return sorted(self._tasks.values(), key=lambda t: t.id)

    def complete(self, task_id: int) -> Task:
        """Mark a task as completed."""
        task = self._tasks.get(task_id)
        if task is None:
            raise ValueError(f"Task {task_id} not found")
        if task.completed:
            raise ValueError(f"Task {task_id} is already completed")
        task.completed = True
        task.completed_at = datetime.now(timezone.utc).isoformat()
        return task

    def delete(self, task_id: int) -> bool:
        """Delete a task. Returns True if deleted, False if not found."""
        if task_id in self._tasks:
            del self._tasks[task_id]
            return True
        return False

    def filter_by_priority(self, priority: str) -> list[Task]:
        """Return all tasks matching the given priority."""
        target = Priority(priority)
        return [t for t in self._tasks.values() if t.priority == target]

    def filter_by_tag(self, tag: str) -> list[Task]:
        """Return all tasks that have the given tag."""
        return [t for t in self._tasks.values() if tag in t.tags]

    def list_sorted(self, sort_by: str = "priority", reverse: bool = False) -> list[Task]:
        """Return tasks sorted by the given field."""
        priority_weight = {
            Priority.CRITICAL: 4,
            Priority.HIGH: 3,
            Priority.MEDIUM: 2,
            Priority.LOW: 1,
        }
        if sort_by == "priority":
            key = lambda t: priority_weight[t.priority]
        elif sort_by == "created_at":
            key = lambda t: t.created_at
        elif sort_by == "title":
            key = lambda t: t.title
        else:
            raise ValueError(f"Unknown sort field: {sort_by}")
        return sorted(self._tasks.values(), key=key, reverse=reverse)

    def stats(self) -> dict:
        """Return statistics about tasks."""
        tasks = list(self._tasks.values())
        completed = sum(1 for t in tasks if t.completed)
        by_priority: dict[str, int] = {}
        for t in tasks:
            p = t.priority.value
            by_priority[p] = by_priority.get(p, 0) + 1
        return {
            "total": len(tasks),
            "completed": completed,
            "pending": len(tasks) - completed,
            "by_priority": by_priority,
        }

    def count(self) -> int:
        """Return total number of tasks."""
        return len(self._tasks)
