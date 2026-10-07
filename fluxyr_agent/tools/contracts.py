from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict
    requires_approval: bool = False
    parallel_safe: bool = False
    side_effecting: bool | None = None
    irreversible: bool = False


@dataclass
class ToolExecutionContext:
    session_id: str
    job_id: str
    emit_progress: Callable | None = None


@dataclass
class ToolResult:
    success: bool
    output: dict | str
    error: str | None = None


class ToolProvider:
    pass
