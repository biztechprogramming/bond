"""Tests for TaskManager."""

from task_manager import TaskManager, Priority


def test_add_task():
    tm = TaskManager()
    task = tm.add("Buy groceries", priority="high", tags=["shopping"])
    assert task.id == 1
    assert task.title == "Buy groceries"
    assert task.priority == Priority.HIGH
    assert task.tags == ["shopping"]
    assert task.completed is False


def test_get_task():
    tm = TaskManager()
    task = tm.add("Test task")
    fetched = tm.get(task.id)
    assert fetched is not None
    assert fetched.title == "Test task"


def test_get_nonexistent():
    tm = TaskManager()
    assert tm.get(999) is None


def test_list_all():
    tm = TaskManager()
    tm.add("First")
    tm.add("Second")
    tm.add("Third")
    tasks = tm.list_all()
    assert len(tasks) == 3
    assert [t.title for t in tasks] == ["First", "Second", "Third"]


def test_complete_task():
    tm = TaskManager()
    task = tm.add("Finish report")
    completed = tm.complete(task.id)
    assert completed.completed is True
    assert completed.completed_at is not None


def test_complete_nonexistent_raises():
    tm = TaskManager()
    try:
        tm.complete(999)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_delete_task():
    tm = TaskManager()
    task = tm.add("Delete me")
    assert tm.delete(task.id) is True
    assert tm.get(task.id) is None


def test_delete_nonexistent():
    tm = TaskManager()
    assert tm.delete(999) is False


def test_count():
    tm = TaskManager()
    assert tm.count() == 0
    tm.add("One")
    tm.add("Two")
    assert tm.count() == 2
    tm.delete(1)
    assert tm.count() == 1
