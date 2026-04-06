"""Tests for tool input validation (Design Doc 094)."""

import pytest
from backend.app.agent.tools.tool_schemas import (
    TOOL_INPUT_SCHEMAS,
    FileReadInput,
    FileWriteInput,
    FileEditInput,
    CodeExecuteInput,
    CodingAgentInput,
    RespondInput,
    WorkPlanInput,
)
from backend.app.agent.tools.tool_result import ValidationResult
from backend.app.agent.iteration_handlers import validate_tool_input


class TestValidationResult:
    def test_ok(self):
        result = ValidationResult.ok()
        assert result.valid is True
        assert result.errors == []
        assert result.to_message_content("test") == ""

    def test_fail(self):
        result = ValidationResult.fail(["field1: required", "field2: must be > 0"])
        assert result.valid is False
        assert len(result.errors) == 2
        msg = result.to_message_content("my_tool")
        assert "Invalid parameters for 'my_tool'" in msg
        assert "field1: required" in msg
        assert "field2: must be > 0" in msg
        assert "Please fix the parameters" in msg


class TestFileReadInput:
    def test_valid_basic(self):
        inp = FileReadInput(path="/some/file.py")
        assert inp.path == "/some/file.py"

    def test_valid_with_lines(self):
        inp = FileReadInput(path="/file.py", line_start=1, line_end=50)
        assert inp.line_start == 1
        assert inp.line_end == 50

    def test_empty_path_rejected(self):
        with pytest.raises(Exception):
            FileReadInput(path="")

    def test_whitespace_path_rejected(self):
        with pytest.raises(Exception):
            FileReadInput(path="   ")

    def test_missing_path_rejected(self):
        with pytest.raises(Exception):
            FileReadInput()

    def test_negative_line_start_rejected(self):
        with pytest.raises(Exception):
            FileReadInput(path="/file.py", line_start=-1)


class TestFileEditInput:
    def test_valid(self):
        inp = FileEditInput(
            path="/file.py",
            edits=[{"old_text": "foo", "new_text": "bar"}],
        )
        assert len(inp.edits) == 1

    def test_empty_edits_rejected(self):
        with pytest.raises(Exception):
            FileEditInput(path="/file.py", edits=[])

    def test_edit_missing_keys_rejected(self):
        with pytest.raises(Exception):
            FileEditInput(path="/file.py", edits=[{"old_text": "foo"}])


class TestCodeExecuteInput:
    def test_valid_python(self):
        inp = CodeExecuteInput(language="python", code="print('hello')")
        assert inp.language == "python"

    def test_valid_shell(self):
        inp = CodeExecuteInput(language="shell", code="ls -la")
        assert inp.language == "shell"

    def test_invalid_language_rejected(self):
        with pytest.raises(Exception):
            CodeExecuteInput(language="javascript", code="console.log('hi')")

    def test_empty_code_rejected(self):
        with pytest.raises(Exception):
            CodeExecuteInput(language="python", code="")

    def test_timeout_bounds(self):
        with pytest.raises(Exception):
            CodeExecuteInput(language="python", code="x", timeout=0)
        with pytest.raises(Exception):
            CodeExecuteInput(language="python", code="x", timeout=301)


class TestCodingAgentInput:
    def test_valid(self):
        inp = CodingAgentInput(
            task="Implement the feature as described in the doc",
            working_directory="/workspace/project",
        )
        assert inp.task.startswith("Implement")

    def test_short_task_rejected(self):
        with pytest.raises(Exception):
            CodingAgentInput(task="do it", working_directory="/workspace")


class TestWorkPlanInput:
    def test_valid_create(self):
        inp = WorkPlanInput(action="create_plan", title="My Plan")
        assert inp.action == "create_plan"

    def test_invalid_action_rejected(self):
        with pytest.raises(Exception):
            WorkPlanInput(action="invalid_action")


class TestValidateToolInput:
    def test_valid_tool_passes(self):
        result = validate_tool_input("file_read", {"path": "/some/file.py"})
        assert result.valid is True

    def test_invalid_tool_fails(self):
        result = validate_tool_input("file_read", {})
        assert result.valid is False
        assert len(result.errors) > 0

    def test_unknown_tool_passes(self):
        """Tools without schemas should pass validation."""
        result = validate_tool_input("unknown_tool_xyz", {"anything": "goes"})
        assert result.valid is True

    def test_error_messages_are_readable(self):
        result = validate_tool_input("code_execute", {"language": "ruby", "code": ""})
        assert result.valid is False
        msg = result.to_message_content("code_execute")
        assert "Invalid parameters for 'code_execute'" in msg

    def test_respond_empty_message_rejected(self):
        result = validate_tool_input("respond", {"message": ""})
        assert result.valid is False


class TestSchemaRegistry:
    def test_all_schemas_are_valid_pydantic_models(self):
        from pydantic import BaseModel
        for name, schema in TOOL_INPUT_SCHEMAS.items():
            assert issubclass(schema, BaseModel), f"{name} schema is not a BaseModel"

    def test_known_tools_have_schemas(self):
        """Critical tools should have schemas."""
        critical_tools = [
            "file_read", "file_write", "file_edit", "code_execute",
            "respond", "say", "coding_agent", "work_plan",
        ]
        for tool in critical_tools:
            assert tool in TOOL_INPUT_SCHEMAS, f"Missing schema for critical tool: {tool}"
