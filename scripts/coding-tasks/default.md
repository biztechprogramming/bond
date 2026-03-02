# Coding Task: Add filtering, sorting, and statistics to TaskManager

## Instructions

You must use your file tools to read and modify files. Do not just describe changes — actually make them using file_read and file_edit.

The code is in `/workspace/bond/tests/coding-workspace/`. Read the files first to understand the codebase.

## Step 1: Read the existing code

Read these files:
- `/workspace/bond/tests/coding-workspace/task_manager.py`
- `/workspace/bond/tests/coding-workspace/test_task_manager.py`

## Step 2: Modify task_manager.py

Add these methods to the `TaskManager` class:

### filter_by_priority(priority: str) -> list[Task]
- Return all tasks matching the given priority string (e.g., "high")
- Return empty list if no matches

### filter_by_tag(tag: str) -> list[Task]
- Return all tasks that have the given tag in their tags list
- Return empty list if no matches

### list_sorted(sort_by: str = "priority", reverse: bool = False) -> list[Task]
- Sort tasks by the given field
- When sort_by is "priority", sort by priority weight: critical=4, high=3, medium=2, low=1
- When sort_by is "created_at", sort by creation timestamp
- When sort_by is "title", sort alphabetically by title
- reverse=True means descending order
- Raise ValueError for unknown sort_by values

### stats() -> dict
- Return a dictionary with:
  - "total": total number of tasks
  - "completed": number of completed tasks
  - "pending": number of non-completed tasks
  - "by_priority": dict mapping priority value strings to counts (e.g., {"high": 2, "low": 1})

### Also modify the existing `complete` method:
- If the task is already completed, raise a ValueError saying it's already completed

## Constraints

- Only modify `/workspace/bond/tests/coding-workspace/task_manager.py`
- Do NOT modify the test file or any other file
- Follow the existing code style
- Do not add new imports — everything you need is already there
