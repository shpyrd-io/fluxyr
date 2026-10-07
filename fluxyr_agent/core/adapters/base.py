"""Abstract base class for AI provider adapters."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from fluxyr_agent.core.utils.token_usage import TokenUsage


class AIProviderAdapter(ABC):
    """Abstract base class for AI provider adapters"""

    def __init__(self, model: str):
        self._model = model

    @abstractmethod
    def get_provider_name(self) -> str:
        """Return the provider name for this adapter"""

    def get_model_name(self) -> str:
        """Return the model name for this adapter"""
        return self._model

    @abstractmethod
    def execute_step(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str | list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        stream_callback: Callable[[Any, Any], None] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Execute one step with the provider's API"""

    def execute_step_with_usage(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str | list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        stream_callback: Callable[[Any, Any], None] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], TokenUsage]:
        """Execute one step with the provider's API and return token usage"""
        response, tools_called = self.execute_step(
            messages=messages,
            system_prompt=system_prompt,
            tools=tools,
            stream=stream,
            stream_callback=stream_callback,
            tool_choice=tool_choice,
        )

        # Get token usage from the last API call
        token_usage = self.get_token_usage()

        return response, tools_called, token_usage

    @abstractmethod
    def format_messages(self, messages: list[dict[str, Any]]) -> Any:
        """Format messages for the provider's API"""

    @abstractmethod
    def format_tools(self, tools: list[dict[str, Any]]) -> Any:
        """Format tools for the provider's API"""

    @abstractmethod
    def get_token_usage(self) -> TokenUsage:
        """Get token usage from the last API call"""
