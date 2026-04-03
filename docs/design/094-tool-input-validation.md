# Design Doc 094: Tool Input Validation

**Status:** Proposed  
**Date:** 2026-04-02  
**Triggered by:** Comparison of Bond agent loop vs Claude Code source — stability improvements

---

## Problem

Bond's tool handlers don't consistently validate inputs before execution. When the model sends a malformed tool call (missing required parameters, wrong types, invalid values):

1. **Crashes deep in the handler** — a `KeyError`, `TypeError`, or `AttributeError` is raised inside the tool implementation
2. **Raw tracebacks leak to the model** — the model receives a Python stack trace instead of an actionable error message
3. **Wasted turns** — the model often can't parse the traceback and retries with the same bad arguments, or gives up entirely
4. **No early rejection** — the tool starts executing (potentially with side effects) before discovering the input is invalid

Claude Code solves this with **Zod schemas** for every tool input and a `ValidationResult` type. Validation happens *before* the tool executes — if inputs are invalid, the model gets a clear error message listing exactly which parameters are wrong and what's expected.

---

## Changes

### 1. Define Pydantic Models for Tool Inputs

**File:** `backend/app/agent/tools/tool_schemas.py` (new file)

```python
from pydantic import BaseModel, Field, field_validator
from typing import Optional


class FileReadInput(BaseModel):
    path: str = Field(..., description="Path to the file to read")
    line_start: Optional[int] = Field(None, ge=1, description="First line to read (1-indexed)")
    line_end: Optional[int] = Field(None, ge=1, description="Last line to read (1-indexed)")
    outline: Optional[bool] = Field(False, description="If true, return function/class outline only")
    
    @field_validator("path")
    @classmethod
    def path_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("path cannot be empty")
        return v


class FileWriteInput(BaseModel):
    path: str = Field(..., description="Path to write the file")
    content: str = Field(..., description="Content to write")
    
    @field_validator("path")
    @classmethod
    def path_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("path cannot be empty")
        return v


class FileEditInput(BaseModel):
    path: str = Field(..., description="Path to the file to edit")
    edits: list[dict] = Field(..., min_length=1, description="List of edit operations")
    
    @field_validator("edits")
    @classmethod
    def edits_have_required_keys(cls, v: list[dict]) -> list[dict]:
        for i, edit in enumerate(v):
            if "old_text" not in edit or "new_text" not in edit:
                raise ValueError(
                    f"Edit {i} must have both 'old_text' and 'new_text' keys"
                )
        return v


class CodeExecuteInput(BaseModel):
    language: str = Field(..., pattern="^(python|shell)$", description="Language: 'python' or 'shell'")
    code: str = Field(..., min_length=1, description="Code to execute")
    timeout: Optional[int] = Field(None, ge=1, le=300, description="Timeout in seconds")


class ShellGrepInput(BaseModel):
    pattern: str = Field(..., min_length=1, description="Search pattern (regex)")
    path: Optional[str] = Field(None, description="Directory or file to search")
    recursive: Optional[bool] = Field(True, description="Search recursively")
    include: Optional[str] = Field(None, description="File glob pattern to include")
    ignore_case: Optional[bool] = Field(False, description="Case-insensitive search")
    max_count: Optional[int] = Field(None, ge=1, description="Max matches to return")
    context_lines: Optional[int] = Field(None, ge=0, description="Context lines around matches")


class ProjectSearchInput(BaseModel):
    query: str = Field(..., min_length=1, description="Search query")
    include: str = Field(..., description="File pattern to include")
    path: Optional[str] = Field(None, description="Directory to search in")
    max_results: Optional[int] = Field(None, ge=1, description="Max results to return")
    type: Optional[str] = Field(None, pattern="^[fd]$", description="'f' for files, 'd' for directories")


class CodingAgentInput(BaseModel):
    task: str = Field(..., min_length=10, description="Task description for the coding agent")
    working_directory: str = Field(..., description="Working directory for the agent")
    timeout_minutes: Optional[int] = Field(None, ge=1, le=60, description="Timeout in minutes")
    branch: Optional[str] = Field(None, description="Git branch to work on")


class RespondInput(BaseModel):
    message: str = Field(..., min_length=1, description="Message to send to the user")


class SayInput(BaseModel):
    message: str = Field(..., min_length=1, description="Interim message to stream to the user")


class WorkPlanInput(BaseModel):
    action: str = Field(..., pattern="^(create_plan|add_item|update_item|complete_plan|get_plan)$")
    plan_id: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    item_id: Optional[str] = None
    status: Optional[str] = None
    ordinal: Optional[int] = None
    notes: Optional[str] = None


# Registry mapping tool names to their input models
TOOL_INPUT_SCHEMAS: dict[str, type[BaseModel]] = {
    "file_read": FileReadInput,
    "file_write": FileWriteInput,
    "file_edit": FileEditInput,
    "code_execute": CodeExecuteInput,
    "shell_grep": ShellGrepInput,
    "project_search": ProjectSearchInput,
    "coding_agent": CodingAgentInput,
    "respond": RespondInput,
    "say": SayInput,
    "work_plan": WorkPlanInput,
}
```

### 2. Define a ValidationResult Type

**File:** `backend/app/agent/tools/tool_result.py` (extend existing — see doc 092)

```python
@dataclass
class ValidationResult:
    """Result of validating tool input before execution."""
    valid: bool
    errors: list[str] = field(default_factory=list)
    
    def to_message_content(self, tool_name: str) -> str:
        """Format validation errors for the model."""
        if self.valid:
            return ""
        error_list = "\n".join(f"  - {e}" for e in self.errors)
        return (
            f"Invalid parameters for '{tool_name}':\n"
            f"{error_list}\n\n"
            f"Please fix the parameters and try again. "
            f"Check the tool definition for required fields and types."
        )
    
    @staticmethod
    def ok() -> "ValidationResult":
        return ValidationResult(valid=True)
    
    @staticmethod
    def fail(errors: list[str]) -> "ValidationResult":
        return ValidationResult(valid=False, errors=errors)
```

### 3. Add a Validation Step Before Tool Execution

**File:** `backend/app/agent/iteration_handlers.py` (modify `execute_tool_call()`)

```python
from backend.app.agent.tools.tool_schemas import TOOL_INPUT_SCHEMAS
from backend.app.agent.tools.tool_result import ValidationResult, ToolResult
from pydantic import ValidationError


def validate_tool_input(tool_name: str, arguments: dict) -> ValidationResult:
    """
    Validate tool arguments against the Pydantic schema.
    
    Returns ValidationResult.ok() if valid or no schema exists.
    Returns ValidationResult.fail() with specific error messages if invalid.
    """
    schema = TOOL_INPUT_SCHEMAS.get(tool_name)
    if schema is None:
        # No schema defined for this tool — allow execution
        logger.debug("no_input_schema", tool=tool_name)
        return ValidationResult.ok()
    
    try:
        schema(**arguments)
        return ValidationResult.ok()
    except ValidationError as e:
        errors = []
        for err in e.errors():
            field_path = " → ".join(str(loc) for loc in err["loc"])
            errors.append(f"{field_path}: {err['msg']}")
        
        logger.warning(
            "tool_input_validation_failed",
            tool=tool_name,
            errors=errors,
            args_keys=list(arguments.keys()),
        )
        return ValidationResult.fail(errors)


async def execute_tool_call(tool_name: str, arguments: dict, **kwargs) -> ToolResult:
    """Execute a tool call with input validation and structured results."""
    
    # Step 1: Validate inputs BEFORE execution
    validation = validate_tool_input(tool_name, arguments)
    if not validation.valid:
        return ToolResult.from_error(
            error=validation.to_message_content(tool_name),
            tool_name=tool_name,
            duration_ms=0,
        )
    
    # Step 2: Execute with timeout and error handling (see doc 092)
    # ... rest of execution logic ...
```

### 4. Register Schemas in the Tool Registry

**File:** `backend/app/agent/tools/native_registry.py`

Extend the tool registration to include the validation schema:

```python
from backend.app.agent.tools.tool_schemas import TOOL_INPUT_SCHEMAS

class NativeToolRegistry:
    """Registry of native tools with their schemas and handlers."""
    
    def register(self, name: str, handler: Callable, description: str, **kwargs):
        """Register a tool with its handler and optional input schema."""
        self._tools[name] = ToolRegistration(
            name=name,
            handler=handler,
            description=description,
            input_schema=TOOL_INPUT_SCHEMAS.get(name),
            **kwargs,
        )
    
    def get_schema(self, name: str) -> type[BaseModel] | None:
        """Get the Pydantic input schema for a tool."""
        reg = self._tools.get(name)
        return reg.input_schema if reg else None
```

---

## Priority & Ordering

| # | Change | Severity | Effort |
|---|--------|----------|--------|
| 1 | Pydantic input schemas | **Foundation** — defines what valid input looks like | 60 min |
| 2 | ValidationResult type | **Foundation** — structured validation feedback | 15 min |
| 3 | Pre-execution validation | **Critical** — catches bad inputs before side effects | 30 min |
| 4 | Registry integration | **Clean-up** — centralizes schema access | 15 min |

---

## Files Affected

- `backend/app/agent/tools/tool_schemas.py` — new file, Pydantic models for all tool inputs
- `backend/app/agent/tools/tool_result.py` — add ValidationResult (extends doc 092)
- `backend/app/agent/iteration_handlers.py` — add validation step before execution
- `backend/app/agent/tools/native_registry.py` — schema-aware registration

---

## Risks

- **Schema drift:** If tool handlers accept new parameters but the schema isn't updated, valid calls get rejected. Mitigation: generate schemas from handler signatures where possible; add integration tests that verify schemas match handlers.
- **Over-strict validation:** Some tools may accept flexible inputs (e.g., `file_edit` with various edit formats). Mitigation: use `Optional` fields and permissive validators; only reject clearly invalid inputs.
- **Performance overhead:** Pydantic validation adds a few milliseconds per call. This is negligible compared to tool execution time.
- **Missing schemas for new tools:** If a new tool is added without a schema, it bypasses validation. Mitigation: `validate_tool_input()` logs a debug message when no schema exists; add a CI check that all registered tools have schemas.

---

## Not Addressed Here

- **Structured tool results** — see doc 092 (Structured Tool Results with Error Feedback)
- **Tool-level timeouts** — see doc 092 (per-tool timeout configuration)
- **Permission/security validation** — whether the user/agent is allowed to call a tool. This is authorization, not input validation.
- **Output validation** — validating that tool results are well-formed. Future work.
