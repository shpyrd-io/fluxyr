"""OpenAI provider adapter implementation."""

import logging
from collections.abc import Callable
from typing import Any

from fluxyr_agent.core.adapters.base import AIProviderAdapter
from fluxyr_agent.core.adapters.openai_execution import (
    execute_non_stream,
    execute_stream,
)
from fluxyr_agent.core.adapters.openai_message_formatter import format_messages
from fluxyr_agent.core.adapters.openai_request_options import (
    build_create_kwargs,
    flatten_system_prompt,
    translate_tool_choice,
)
from fluxyr_agent.core.adapters.openai_usage import (
    build_usage_dict,
    carve_out_nested_token_counts,
)
from fluxyr_agent.core.utils.exceptions import ProviderError
from fluxyr_agent.core.utils.token_usage import TokenUsage

logger = logging.getLogger(__name__)


class OpenAIAdapter(AIProviderAdapter):
    """OpenAI-compatible provider adapter (OpenAI, OpenRouter, etc.).

    Supports streaming, tool-use, and usage metering via an optional
    usage_callback, so structured.complete() can meter tokens uniformly
    across providers. Streaming emits ANTHROPIC-SHAPED synthetic events (see
    openai_stream_translator) because every stream consumer in this codebase
    dispatches on Anthropic SDK event shapes.
    """

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        usage_callback: Callable[[dict], None] | None = None,
        provider: str = "openai",
        max_tokens: int | None = None,
        reasoning: dict[str, Any] | None = None,
        provider_preferences: dict[str, Any] | None = None,
    ):
        """Initialise the OpenAIAdapter.

        Args:
            model: Model identifier forwarded to the OpenAI API.
            api_key: API key for the upstream provider; None falls back to
                the OPENAI_API_KEY environment variable.
            base_url: Override the base URL for OpenAI-compatible providers
                (e.g. 'https://openrouter.ai/api/v1'); None uses the SDK default.
            usage_callback: Optional callable invoked once after each step
                with a usage_dict in the canonical cross-adapter shape — see
                ``openai_usage.build_usage_dict``.
            provider: Logical provider name emitted in the usage_dict. Defaults
                to 'openai'; pass 'openrouter' when routing through OpenRouter
                so billing_emitters resolves the correct Provider enum value.
            max_tokens: Output-token ceiling sent as ``max_tokens``. None
                omits the parameter. Exposed via the ``max_tokens`` property
                so SyntheticBrain.save() can persist ``adapter_max_tokens``.
            reasoning: Optional OpenRouter reasoning object (e.g.
                ``{'effort': 'high'}``), sent through ``extra_body``. None omits it.
            provider_preferences: Optional OpenRouter provider-routing object,
                sent through ``extra_body['provider']``. None omits it.
        """
        super().__init__(model)
        try:
            from openai import OpenAI

            client_kwargs: dict = {"timeout": 120.0, "max_retries": 1}
            if api_key is not None:
                client_kwargs["api_key"] = api_key
            if base_url is not None:
                client_kwargs["base_url"] = base_url
            self.client = OpenAI(**client_kwargs)
        except ImportError:
            raise ImportError(
                "OpenAI package is required for OpenAIAdapter. "
                "Install with 'pip install openai'."
            )
        self._last_response = None
        # Accumulated across a stream's chunks (see _execute_stream); stays
        # None non-stream, where build_usage_dict reads response.id instead.
        self._last_request_id: str | None = None
        self._usage_callback = usage_callback
        self._provider = provider
        self._max_tokens = max_tokens
        self._reasoning = reasoning
        self._provider_preferences = provider_preferences

    @property
    def max_tokens(self) -> int | None:
        """Return the configured max_tokens ceiling, or None when unset."""
        return self._max_tokens

    def get_provider_name(self) -> str:
        return "openai"

    def format_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Format messages for the OpenAI Chat API."""
        return format_messages(messages)

    def format_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Format tools for the OpenAI Chat API."""
        if not tools:
            return []
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters", {}),
                },
            }
            for tool in tools
        ]

    def execute_step(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str | list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        stream_callback: Callable[[Any, Any], None] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Execute one step with the OpenAI Chat API.

        ``tool_choice`` is translated from the Anthropic shape by
        ``translate_tool_choice``, which raises ValueError on an
        unrecognised value before any request is issued.
        """
        # Reset before ANY work begins: translate_tool_choice and the
        # formatting calls below can all raise before a request is ever
        # sent, and execute_step_with_usage bills from a `finally` — a
        # stale _last_response from a PRIOR step on a reused adapter would
        # otherwise be re-billed when THIS step fails early.
        self._last_response = None
        self._last_request_id = None
        wire_tool_choice = translate_tool_choice(tool_choice)
        try:
            formatted_messages = self.format_messages(messages)
            formatted_tools = self.format_tools(tools) if tools else None

            prompt_text = flatten_system_prompt(system_prompt)
            if prompt_text:
                system_message = {"role": "system", "content": prompt_text}
                if formatted_messages and formatted_messages[0].get("role") == "system":
                    formatted_messages[0] = system_message
                else:
                    formatted_messages.insert(0, system_message)

            kwargs = build_create_kwargs(
                model=self._model,
                formatted_messages=formatted_messages,
                formatted_tools=formatted_tools,
                wire_tool_choice=wire_tool_choice,
                stream=stream,
                max_tokens=self._max_tokens,
                reasoning=self._reasoning,
                provider_preferences=self._provider_preferences,
                provider=self._provider,
            )

            if stream:
                return self._execute_stream(kwargs, stream_callback)
            return self._execute_non_stream(kwargs)

        except Exception as exc:
            # _last_response is NOT cleared here: usage captured mid-call
            # (via on_usage/on_response, before this exception) must survive
            # so execute_step_with_usage's `finally` can still meter it.
            raise ProviderError(f"Error calling OpenAI API: {exc!s}") from exc

    def _execute_stream(
        self,
        create_kwargs: dict[str, Any],
        stream_callback: Callable[[Any, Any], None] | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Handle streaming mode for OpenAI. See openai_execution.execute_stream.

        _last_response is reset by execute_step before this is reached.
        """
        usage_seen = False

        def _on_usage(chunk: Any, request_id: str | None) -> None:
            # Promoted instantly (not after the loop ends) so a later failure
            # (e.g. a connection reset before [DONE]) can't drop it. The FULL
            # chunk is stored (not just chunk.usage) so build_usage_dict can
            # also read OpenRouter's extra cost/serving-provider fields — see
            # execute_stream's on_usage docstring. request_id is the
            # ACCUMULATED id tracked across every chunk (not just this
            # winning chunk's own .id), stored separately so a winning chunk
            # with no id of its own is still billed under an earlier one.
            nonlocal usage_seen
            usage_seen = True
            self._last_response = chunk
            self._last_request_id = request_id

        result = execute_stream(
            self.client, create_kwargs, stream_callback, on_usage=_on_usage
        )
        # Success path only: a provider error already explains why nothing
        # was metered, and this is only a signal when metering is intended.
        if not usage_seen and self._usage_callback is not None:
            logger.warning(
                "OpenAIAdapter: stream ended with no usage; turn will not be "
                "metered (model=%s provider=%s)",
                self._model,
                self._provider,
            )
        return result

    def _execute_non_stream(
        self,
        create_kwargs: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Handle non-streaming mode for OpenAI. See openai_execution.execute_non_stream."""

        def _on_response(response_obj: Any) -> None:
            # Promoted before tool-call argument JSON is parsed, which can
            # raise on a malformed payload.
            self._last_response = response_obj

        return execute_non_stream(self.client, create_kwargs, on_response=_on_response)

    def get_token_usage(self) -> TokenUsage:
        """Return token usage from the last API call.

        Built from ``carve_out_nested_token_counts`` — the SAME reader
        ``build_usage_dict`` bills from — so a dict-shaped ``usage`` object
        reads correctly instead of degrading to 0/0, and ``input_tokens``
        here intentionally EXCLUDES cached tokens (matching what the ledger
        bills as INPUT_TOKENS) so a ``chat.turn.finished`` log line agrees
        with the UsageEvent rows for the same turn.
        """
        if (
            self._last_response
            and hasattr(self._last_response, "usage")
            and self._last_response.usage is not None
        ):
            split = carve_out_nested_token_counts(self._last_response.usage)
            return TokenUsage(
                input_tokens=split["input_tokens"],
                output_tokens=split["output_tokens"],
                provider=self._provider,
            )
        return TokenUsage(
            input_tokens=0, output_tokens=0, provider=f"{self._provider} (no data)"
        )

    def execute_step_with_usage(
        self,
        messages: list[dict[str, Any]],
        system_prompt: Any,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        stream_callback: Callable[[Any, Any], None] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], TokenUsage]:
        """Execute one step and invoke the usage_callback when configured.

        The callback fires from ``finally`` so it still runs when
        ``execute_step`` raises AFTER usage was already captured (see
        _execute_stream/_execute_non_stream's on_usage/on_response).
        """
        try:
            response, tools_called, token_usage = super().execute_step_with_usage(
                messages=messages,
                system_prompt=system_prompt,
                tools=tools,
                stream=stream,
                stream_callback=stream_callback,
                tool_choice=tool_choice,
            )
        finally:
            self._fire_usage_callback()

        return response, tools_called, token_usage

    def _fire_usage_callback(self) -> None:
        """Invoke the usage_callback with self._last_response's usage_dict.

        No-op when no callback is configured or no usage was captured. Runs
        from execute_step_with_usage's ``finally``, so building the
        usage_dict AND invoking the callback share one try/except — a
        malformed usage object must not replace an in-flight provider
        exception or fail an otherwise-successful turn.
        """
        if self._usage_callback is None:
            return
        try:
            usage_dict = build_usage_dict(
                provider=self._provider,
                model=self._model,
                last_response=self._last_response,
                request_id=self._last_request_id,
            )
            if usage_dict is None:
                return
            self._usage_callback(usage_dict)
        except Exception:
            # WARNING (not DEBUG): a failing metering callback means this turn's
            # usage is silently dropped from billing. That must be visible in
            # production logs, not only when DEBUG logging happens to be on.
            logger.warning(
                "OpenAIAdapter: usage_callback raised an exception (ignored)",
                exc_info=True,
            )
