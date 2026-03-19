"""Tests for outcome signal collection (Design Doc 050)."""

from __future__ import annotations

import pytest

from backend.app.agent.outcome import classify_task, detect_user_correction


# ── classify_task ──


def test_classify_task_tool_based():
    assert classify_task("help me", ["code_execute", "file_read"]) == "coding"
    assert classify_task("find info", ["web_search"]) == "research"
    assert classify_task("read that file", ["file_read"]) == "file_ops"


def test_classify_task_keyword_based():
    assert classify_task("debug this function please", []) == "coding"
    assert classify_task("search for documentation about X", []) == "research"
    assert classify_task("create a new directory for logs", []) == "file_ops"


def test_classify_task_fallback():
    assert classify_task("hello how are you", []) == "chat"
    assert classify_task("", []) == "chat"


# ── detect_user_correction ──


def test_correction_true_positive():
    assert detect_user_correction("that's wrong, try again") is True
    assert detect_user_correction("no, I said the other file") is True
    assert detect_user_correction("wrong file please undo") is True
    assert detect_user_correction("actually, I wanted something else") is True
    assert detect_user_correction("stop doing that") is True


def test_correction_false_positive_avoided():
    assert detect_user_correction("no problem at all") is False
    assert detect_user_correction("no worries, take your time") is False
    assert detect_user_correction("nothing wrong with that") is False
    assert detect_user_correction("not bad at all") is False
    assert detect_user_correction("no thanks") is False


def test_correction_short_message():
    assert detect_user_correction("hi") is False
    assert detect_user_correction("ok") is False


def test_correction_empty():
    assert detect_user_correction("") is False
    assert detect_user_correction(None) is False
