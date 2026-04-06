"""Pydantic input schemas for tool validation (Design Doc 094).

Each tool gets a Pydantic model that validates its inputs before execution.
This catches malformed tool calls early and gives the model clear error messages
instead of raw tracebacks from deep inside tool handlers.
"""

from __future__ import annotations

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
    agent_type: Optional[str] = Field(None, pattern="^(claude|codex|pi)$", description="Agent type")


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
    files_changed: Optional[list[str]] = None
    context_snapshot: Optional[dict] = None


class FileSmartEditInput(BaseModel):
    path: str = Field(..., description="Path to the file to edit")
    search: str = Field(..., min_length=1, description="Text pattern to search for")
    new_content: Optional[str] = Field(None, description="Replacement content")
    end_search: Optional[str] = Field(None, description="End boundary for the section")
    occurrence: Optional[int] = Field(None, ge=1, description="Which occurrence to match")
    lines_before: Optional[int] = Field(None, ge=0, description="Context lines before match")
    lines_after: Optional[int] = Field(None, ge=0, description="Context lines after match")


class FileListInput(BaseModel):
    path: Optional[str] = Field(None, description="Directory path")
    mode: Optional[str] = Field(None, pattern="^(list|find|tree|count)$", description="Operation mode")
    name: Optional[str] = Field(None, description="Filename pattern")
    regex: Optional[str] = Field(None, description="Regex pattern for filenames")
    type: Optional[str] = Field(None, pattern="^[fdl]$", description="'f' files, 'd' dirs, 'l' links")
    max_depth: Optional[int] = Field(None, ge=1, description="Max directory depth")
    exclude: Optional[list[str]] = Field(None, description="Patterns to exclude")
    all: Optional[bool] = Field(None, description="Show hidden files")
    long: Optional[bool] = Field(None, description="Long listing format")
    dirs_only: Optional[bool] = Field(None, description="Show directories only")
    wc_mode: Optional[str] = Field(None, pattern="^(lines|words|chars)$", description="Word count mode")


class FileSearchInput(BaseModel):
    pattern: Optional[str] = Field(None, description="Search pattern")
    path: Optional[str] = Field(None, description="Directory to search")
    include: Optional[str] = Field(None, description="File glob to include")
    ignore_case: Optional[bool] = Field(None, description="Case-insensitive search")
    max_results: Optional[int] = Field(None, ge=1, description="Max results")
    max_count: Optional[int] = Field(None, ge=1, description="Max matches per file")
    context_lines: Optional[int] = Field(None, ge=0, description="Context lines")
    recursive: Optional[bool] = Field(None, description="Search recursively")
    mode: Optional[str] = Field(None, pattern="^(content|project)$", description="Search mode")
    query: Optional[str] = Field(None, description="Project search query")
    type: Optional[str] = Field(None, pattern="^[fd]$", description="File type filter")


class GenerateImageInput(BaseModel):
    prompt: str = Field(..., min_length=1, description="Image generation prompt")
    filename: Optional[str] = Field(None, description="Output filename")
    size: Optional[str] = Field(None, pattern="^(256x256|512x512|1024x1024|1024x1536|1536x1024)$")
    style: Optional[str] = Field(None, pattern="^(natural|vivid|anime|photographic|digital-art|pixel-art|icon)$")
    provider: Optional[str] = Field(None, pattern="^(openai|replicate|comfyui)$")
    model: Optional[str] = Field(None, description="Model name")
    count: Optional[int] = Field(None, ge=1, le=4, description="Number of images")


class MemorySaveInput(BaseModel):
    content: str = Field(..., min_length=1, description="Content to save to memory")
    tags: Optional[list[str]] = Field(None, description="Tags for the memory")


class SearchMemoryInput(BaseModel):
    query: str = Field(..., min_length=1, description="Search query for memory")
    limit: Optional[int] = Field(None, ge=1, description="Max results")


class LoadContextInput(BaseModel):
    category: str = Field(..., min_length=1, description="Context category to load")


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
    "file_smart_edit": FileSmartEditInput,
    "file_list": FileListInput,
    "file_search": FileSearchInput,
    "generate_image": GenerateImageInput,
    "memory_save": MemorySaveInput,
    "search_memory": SearchMemoryInput,
    "load_context": LoadContextInput,
}
