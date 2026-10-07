"""Tool-related classes for the SyntheticBrain system."""

from collections.abc import Callable
from typing import Any, TypedDict


class ToolResponse:
    """Standard format for tool responses"""

    def __init__(
        self,
        tool_name: str,
        call_id: str,
        result: dict[str, Any],
        mode: str = "continue",
    ):
        self.tool_name = tool_name
        self.call_id = call_id
        self.result = result
        self.mode = mode  # "continue" or "wait"

        if mode not in ["continue", "wait"]:
            raise ValueError("Mode must be 'continue' or 'wait'")

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dictionary"""
        return {
            "tool_name": self.tool_name,
            "call_id": self.call_id,
            "result": self.result,
            "mode": self.mode,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolResponse":
        """Create from dictionary"""
        return cls(
            tool_name=data["tool_name"],
            call_id=data["call_id"],
            result=data["result"],
            mode=data["mode"],
        )


# Type definition for a tool with function
class ToolDefinition(TypedDict, total=False):
    """Type definition for a complete tool definition including function reference"""

    name: str
    type: str
    description: str
    parameters: dict[str, Any]
    function: Callable  # The actual function to execute
