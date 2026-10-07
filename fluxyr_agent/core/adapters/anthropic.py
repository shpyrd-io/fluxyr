"""Anthropic provider adapter implementation.

SECURITY NOTE: The ``file_path`` values passed through user message content
are read from disk verbatim.  Callers are responsible for ensuring that all
``file_path`` values originate from trusted sources (e.g. server-side file
references) and are never derived from raw user input without sanitisation.
"""

import logging
import os
from collections.abc import Callable
from typing import Any

from fluxyr_agent.core.adapters.base import AIProviderAdapter
from fluxyr_agent.core.utils.exceptions import ProviderError
from fluxyr_agent.core.utils.token_usage import TokenUsage

DEFAULT_MODEL = "claude-sonnet-4-6"

logger = logging.getLogger(__name__)


def _validate_file_path(file_path: str) -> str:
    """Validate and resolve a file_path for safe reading.

    Raises ValueError if the path is unsafe. Returns the resolved absolute path.
    Callers are responsible for ensuring file_path values are trusted.
    """
    resolved = os.path.realpath(file_path)
    if not os.path.isabs(resolved):
        raise ValueError(f"Unsafe file_path: {file_path!r}")
    # Allowlist: only paths under /tmp or a configured upload directory are permitted
    allowed_prefixes = ["/tmp"]
    upload_dir = os.environ.get("UPLOAD_DIR", "")
    if upload_dir:
        allowed_prefixes.append(os.path.realpath(upload_dir))
    if not any(resolved.startswith(prefix) for prefix in allowed_prefixes):
        raise ValueError(
            f"file_path {file_path!r} is outside allowed directories. "
            f"Set UPLOAD_DIR env var to configure the allowed upload directory."
        )
    return resolved


# Unrecognised content blocks are logged once per (adapter, type) per process.
# The alternative is one line per block per iteration — up to 25 iterations in a
# single chat turn — which buries the signal it exists to raise.
_WARNED_BLOCK_TYPES: set[tuple[str, str]] = set()


def _warn_once(adapter: str, content_type: str) -> None:
    """Log an unknown content block the first time this process sees it."""
    key = (adapter, content_type)
    if key in _WARNED_BLOCK_TYPES:
        return
    _WARNED_BLOCK_TYPES.add(key)
    logger.warning(
        "%s adapter: unsupported content block %r — emitted as a visible stub",
        adapter,
        content_type,
    )


class AnthropicAdapter(AIProviderAdapter):
    """Anthropic-specific implementation of AIProviderAdapter."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        max_tokens: int = 64000,
        usage_callback: Callable[[dict], None] | None = None,
        thinking_budget: int = 10000,
        thinking_mode: str = "adaptive",
    ):
        super().__init__(model)

        try:
            import anthropic

            self._anthropic = anthropic
            self.client = anthropic.Anthropic(
                api_key=api_key, timeout=120.0, max_retries=1
            )
        except ImportError:
            raise ImportError(
                "anthropic package is required for AnthropicAdapter. "
                "Install with 'pip install anthropic'."
            )

        self._last_usage: tuple[int, int] | None = None
        self._max_tokens = max_tokens
        self._usage_callback = usage_callback
        self._thinking_budget = thinking_budget
        # Anthropic's two thinking modes are mutually exclusive and each model
        # accepts exactly one; sending the wrong one is a hard 400.
        self._thinking_mode = thinking_mode

    def get_provider_name(self) -> str:
        return "anthropic"

    @property
    def max_tokens(self) -> int:
        """Return the configured max_tokens ceiling for this adapter."""
        return self._max_tokens

    def _clean_orphaned_tool_results(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Remove orphaned tool_use/tool_result blocks caused by short-term memory truncation.

        Two passes are run:
        - Pass 1: Remove tool_result blocks with no matching tool_use in the preceding
          assistant message (the tool_use was truncated away).
        - Pass 2: Remove tool_use blocks from assistant messages with no matching tool_result
          in the immediately following user message (the tool_result was truncated away).
        Both conditions cause Anthropic 400 errors.
        """
        # Pass 1: strip orphaned tool_result blocks (no preceding tool_use)
        pass1: list[dict[str, Any]] = []
        for msg in messages:
            if msg.get("role") == "user":
                content = msg.get("content", [])
                if isinstance(content, list):
                    preceding_tool_ids: set = set()
                    if pass1 and pass1[-1].get("role") == "assistant":
                        prev_content = pass1[-1].get("content", [])
                        if isinstance(prev_content, list):
                            for block in prev_content:
                                if block.get("type") == "tool_use":
                                    preceding_tool_ids.add(block.get("id", ""))

                    valid_blocks = [
                        block
                        for block in content
                        if block.get("type") != "tool_result"
                        or block.get("tool_use_id") in preceding_tool_ids
                    ]
                    if valid_blocks:
                        pass1.append({"role": "user", "content": valid_blocks})
                    else:
                        logger.warning(
                            "Dropping orphaned user message — all content blocks were orphaned tool_results"
                        )
                else:
                    pass1.append(msg)
            else:
                pass1.append(msg)

        # Pass 2: strip orphaned tool_use blocks (no following tool_result)
        pass2: list[dict[str, Any]] = []
        for i, msg in enumerate(pass1):
            if msg.get("role") == "assistant":
                content = msg.get("content", [])
                if isinstance(content, list):
                    following_tool_result_ids: set = set()
                    if i + 1 < len(pass1):
                        next_msg = pass1[i + 1]
                        if next_msg.get("role") == "user":
                            next_content = next_msg.get("content", [])
                            if isinstance(next_content, list):
                                for block in next_content:
                                    if block.get("type") == "tool_result":
                                        following_tool_result_ids.add(
                                            block.get("tool_use_id", "")
                                        )

                    valid_blocks = [
                        block
                        for block in content
                        if block.get("type") != "tool_use"
                        or block.get("id", "") in following_tool_result_ids
                    ]
                    if valid_blocks:
                        pass2.append({"role": "assistant", "content": valid_blocks})
                    else:
                        logger.warning(
                            "Dropping orphaned assistant message — all content blocks were orphaned tool_uses"
                        )
                else:
                    pass2.append(msg)
            else:
                pass2.append(msg)

        return pass2

    def _extract_content_blocks(
        self, content_blocks: list
    ) -> tuple[str, list[dict], list[dict], bool]:
        """Extract response_text, tools_called, content_list, and has_thinking from Anthropic content blocks."""
        response_text = ""
        tools_called = []
        content_list = []
        has_thinking = False
        for block in content_blocks:
            btype = getattr(block, "type", "")
            if btype == "thinking":
                has_thinking = True
                block_dict: dict = {
                    "type": "thinking",
                    "thinking": getattr(block, "thinking", ""),
                }
                sig = getattr(block, "signature", "")
                if sig:
                    block_dict["signature"] = sig
                content_list.append(block_dict)
            elif btype == "redacted_thinking":
                # redacted_thinking blocks carry opaque encrypted data that must be persisted
                # and round-tripped unmodified for multi-turn correctness; they are not rendered.
                has_thinking = True
                content_list.append(
                    {"type": "redacted_thinking", "data": getattr(block, "data", "")}
                )
            elif btype == "text":
                response_text += block.text
                content_list.append({"type": "text", "text": block.text})
            elif btype == "tool_use":
                tools_called.append(
                    {
                        "name": block.name,
                        "call_id": block.id,
                        "arguments": block.input,
                    }
                )
                content_list.append(
                    {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    }
                )
        return response_text, tools_called, content_list, has_thinking

    def _rebuild_content_blocks(self, items: list) -> list:
        """Rebuild Anthropic API content blocks from stored structured format."""
        blocks = []
        for item in items:
            if not isinstance(item, dict):
                continue
            btype = item.get("type")
            if btype == "thinking":
                # Pass thinking blocks through — include signature if present, DO NOT add cache_control
                block = {"type": "thinking", "thinking": item.get("thinking", "")}
                if item.get("signature"):
                    block["signature"] = item["signature"]
                blocks.append(block)
            elif btype == "redacted_thinking":
                # Round-trip opaque encrypted data unmodified; DO NOT add cache_control
                blocks.append(
                    {"type": "redacted_thinking", "data": item.get("data", "")}
                )
            elif btype == "text":
                blocks.append({"type": "text", "text": item.get("text", "")})
            elif btype == "tool_use":
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": item.get("id", ""),
                        "name": item.get("name", ""),
                        "input": item.get("input", {}),
                    }
                )
        return blocks

    def format_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Format messages for the Anthropic Messages API.

        Key differences from OpenAI:
        - System messages are passed separately (skipped here).
        - Tool use is a content block in the assistant message.
        - Tool results are user-role messages with tool_result content blocks.
        - Files passed via is_path=True are read and sent as text blocks.
        - Orphaned tool_result blocks (no matching preceding tool_use) are stripped.
        """
        formatted = []

        for message in messages:
            role = message.get("role", "")

            if role == "system":
                # Handled separately in execute_step; skip here.
                continue

            elif role == "user":
                content = message.get("content", "")
                if isinstance(content, list):
                    blocks = []
                    for item in content:
                        content_type = item.get("content_type", item.get("type", ""))

                        if content_type == "text":
                            blocks.append(
                                {"type": "text", "text": item.get("text", "")}
                            )

                        elif content_type == "image":
                            if item.get("is_url", False):
                                blocks.append(
                                    {
                                        "type": "image",
                                        "source": {
                                            "type": "url",
                                            "url": item.get("image_data", ""),
                                        },
                                    }
                                )
                            else:
                                blocks.append(
                                    {
                                        "type": "image",
                                        "source": {
                                            "type": "base64",
                                            "media_type": item.get(
                                                "mime_type", "image/jpeg"
                                            ),
                                            "data": item.get("image_data", ""),
                                        },
                                    }
                                )

                        elif content_type == "document":
                            blocks.append(
                                {
                                    "type": "document",
                                    "title": item.get("file_name", "document"),
                                    "source": {
                                        "type": "base64",
                                        "media_type": item.get(
                                            "mime_type", "application/pdf"
                                        ),
                                        "data": item.get("data", ""),
                                    },
                                }
                            )

                        elif content_type == "file":
                            # Read file from path when is_path=True; otherwise treat as text.
                            if item.get("is_path") and item.get("file_path"):
                                resolved_path = _validate_file_path(item["file_path"])
                                try:
                                    with open(
                                        resolved_path, "r", encoding="utf-8"
                                    ) as fh:
                                        file_text = fh.read()
                                except Exception:
                                    file_text = f"[Could not read file: {item.get('file_name', resolved_path)}]"
                                blocks.append(
                                    {
                                        "type": "text",
                                        "text": f"[File: {item.get('file_name', 'file')}]\n{file_text}",
                                    }
                                )
                            else:
                                blocks.append(
                                    {
                                        "type": "text",
                                        "text": f"[File attachment: {item.get('file_name', 'unknown')}]",
                                    }
                                )

                        else:
                            # Closed set. Silently dropping a block it does not
                            # recognise is how a formatter turns an unknown
                            # shape into a message the model never sees, or —
                            # if it was the only block — into an empty content
                            # list the API rejects with a 400 that names
                            # nothing useful. Emit something visible instead.
                            _warn_once("anthropic", content_type)
                            blocks.append(
                                {
                                    "type": "text",
                                    "text": f"[Unsupported content block: {content_type}]",
                                }
                            )

                    if not blocks:
                        # The invariant is that a user message never reaches the
                        # wire with empty content. Guarding the OUTPUT covers an
                        # empty input list too, which the branches above cannot.
                        blocks = [
                            {"type": "text", "text": "[Attachment content unavailable]"}
                        ]
                    formatted.append({"role": "user", "content": blocks})
                else:
                    formatted.append({"role": "user", "content": content})

            elif (
                role == "assistant"
                and isinstance(message.get("content"), list)
                and "function_calls" not in message
            ):
                # Native Anthropic shape — content already formatted as typed blocks.
                # Pass thinking blocks through as-is; DO NOT add cache_control to them.
                blocks = self._rebuild_content_blocks(message["content"])
                if blocks:
                    formatted.append({"role": "assistant", "content": blocks})

            elif role == "assistant" and "function_calls" not in message:
                formatted.append(
                    {"role": "assistant", "content": message.get("content", "")}
                )

            elif role == "assistant" and "function_calls" in message:
                blocks = []
                content = message.get("content", "")
                if isinstance(content, list):
                    # Native Anthropic shape with thinking blocks alongside tool calls.
                    # Preserve thinking and text blocks in order; tool_use blocks will be
                    # added below from function_calls (the canonical source of truth).
                    # Skip tool_use blocks — they are reconstructed from function_calls below.
                    non_tool_items = [
                        i
                        for i in content
                        if isinstance(i, dict) and i.get("type") != "tool_use"
                    ]
                    blocks = self._rebuild_content_blocks(non_tool_items)
                elif content:
                    blocks.append({"type": "text", "text": content})
                for fc in message["function_calls"]:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": fc.get("call_id", ""),
                            "name": fc.get("name", ""),
                            "input": fc.get("arguments", {}),
                        }
                    )
                formatted.append({"role": "assistant", "content": blocks})

            elif role == "tool":
                # Anthropic expects all tool_results for the same assistant turn to be
                # batched into a single user message. Consecutive tool results are merged.
                tool_result_block = {
                    "type": "tool_result",
                    "tool_use_id": message.get("tool_call_id", ""),
                    "content": message.get("content", "{}"),
                }
                if (
                    formatted
                    and formatted[-1].get("role") == "user"
                    and isinstance(formatted[-1].get("content"), list)
                    and formatted[-1]["content"]
                    and formatted[-1]["content"][0].get("type") == "tool_result"
                ):
                    formatted[-1]["content"].append(tool_result_block)
                else:
                    formatted.append({"role": "user", "content": [tool_result_block]})

            elif message.get("type") == "function_call_output":
                formatted.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": message.get("call_id", ""),
                                "content": message.get("output", "{}"),
                            }
                        ],
                    }
                )

        cleaned = self._clean_orphaned_tool_results(formatted)

        # Mark the last content block of the penultimate *text-bearing* user message
        # as a cache boundary.  Tool-result-only messages are skipped because they are
        # unique to each conversation turn and will never produce a cache hit — marking
        # them wastes a cache write at a 25 % cost premium.
        # A message is tool-result-only when its content is a non-empty list whose
        # every block is of type "tool_result".  String content and empty lists are
        # treated as text-bearing (not tool-result-only) and are included.
        cacheable_user_indices = [
            i
            for i, m in enumerate(cleaned)
            if m.get("role") == "user"
            and not (
                isinstance(m.get("content"), list)
                and bool(m.get("content"))
                and all(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in m["content"]
                )
            )
        ]
        if len(cacheable_user_indices) >= 2:
            penultimate = cleaned[cacheable_user_indices[-2]]
            content = penultimate.get("content")
            if isinstance(content, list) and content:
                content[-1]["cache_control"] = {"type": "ephemeral"}
            elif isinstance(content, str) and content:
                cleaned[cacheable_user_indices[-2]] = {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": content,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                }

        return cleaned

    def format_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Format tools for the Anthropic Messages API.

        Anthropic uses ``input_schema`` where OpenAI uses ``parameters``.
        The last tool receives a ``cache_control`` block to enable prompt caching.
        """
        if not tools:
            return []

        result = [
            {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "input_schema": tool.get("parameters", {}),
            }
            for tool in tools
        ]
        result[-1]["cache_control"] = {"type": "ephemeral"}
        return result

    _TRUNCATION_MARKER = "\n\n_[resposta truncada no limite de tokens]_"

    def _append_truncation_marker(
        self,
        response_text: str,
        content_list: list,
        has_thinking: bool,
    ) -> tuple:
        """Append the truncation marker to response_text and the last text block in content_list.

        Returns updated (response_text, content_list).
        """
        response_text = response_text + self._TRUNCATION_MARKER
        # Also update the last text block in content_list so the marker survives
        # when has_thinking=True and the list shape is used.
        updated_list = list(content_list)
        for i in range(len(updated_list) - 1, -1, -1):
            if (
                isinstance(updated_list[i], dict)
                and updated_list[i].get("type") == "text"
            ):
                updated_list[i] = dict(updated_list[i])
                updated_list[i]["text"] = (
                    updated_list[i]["text"] + self._TRUNCATION_MARKER
                )
                break
        else:
            # No text block exists; append one so the marker is always visible.
            updated_list.append(
                {"type": "text", "text": self._TRUNCATION_MARKER.strip()}
            )
        return response_text, updated_list

    def _thinking_payload(self):
        """Build the ``thinking`` request field, or None to omit it.

        'adaptive'  -> {'type': 'adaptive'}          (newer models)
        'extended'  -> {'type': 'enabled', 'budget_tokens': N}
        'none'      -> omitted entirely

        The budget is clamped to Anthropic's 1024 minimum and kept strictly below
        max_tokens, which the API requires.
        """
        # Read defensively: adapters are also built by from_saved_state and by
        # tests that bypass __init__, and a missing mode must degrade to the
        # previous behaviour rather than raise.
        mode = getattr(self, "_thinking_mode", "adaptive")
        if mode == "none" or (self._thinking_budget <= 0 and mode != "adaptive"):
            return None
        if mode in ("extended", "enabled"):
            ceiling = max(1024, (self._max_tokens or 4096) - 512)
            return {
                "type": "enabled",
                "budget_tokens": max(1024, min(self._thinking_budget, ceiling)),
            }
        return {"type": "adaptive"}

    def execute_step(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str | list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        stream_callback: Callable[[Any, Any], None] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Execute one step with the Anthropic Messages API."""
        try:
            formatted_messages = self.format_messages(messages)
            formatted_tools = self.format_tools(tools) if tools else None

            # Anthropic requires at least one user message. When the caller
            # (e.g. MemoryManager) bundles all context into a system message and
            # passes an empty messages list, inject a minimal trigger so the API
            # can proceed.
            if not formatted_messages:
                formatted_messages = [
                    {
                        "role": "user",
                        "content": "Please analyze and extract important information.",
                    }
                ]

            kwargs: dict[str, Any] = dict(
                model=self._model,
                max_tokens=self._max_tokens,
                messages=formatted_messages,
            )
            kwargs["system"] = system_prompt
            thinking = self._thinking_payload()
            if thinking is not None:
                kwargs["thinking"] = thinking
            if formatted_tools:
                kwargs["tools"] = formatted_tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice

            if stream:
                with self.client.messages.stream(**kwargs) as stream_obj:
                    for event in stream_obj:
                        if stream_callback:
                            stream_callback(event, None)

                    final_msg = stream_obj.get_final_message()
                    self._last_usage = (
                        final_msg.usage.input_tokens,
                        final_msg.usage.output_tokens,
                    )
                    if self._usage_callback is not None:
                        try:
                            self._usage_callback(
                                {
                                    "provider": "anthropic",
                                    "model": self._model,
                                    "input_tokens": final_msg.usage.input_tokens,
                                    "output_tokens": final_msg.usage.output_tokens,
                                    "cache_read_tokens": getattr(
                                        final_msg.usage, "cache_read_input_tokens", 0
                                    )
                                    or 0,
                                    "cache_write_tokens": getattr(
                                        final_msg.usage,
                                        "cache_creation_input_tokens",
                                        0,
                                    )
                                    or 0,
                                    "request_id": getattr(final_msg, "id", None),
                                }
                            )
                        except Exception:
                            logger.debug(
                                "usage_callback raised an exception (ignored)",
                                exc_info=True,
                            )
                    # Use final_msg as the authoritative source; streaming deltas
                    # are not accumulated to avoid double-counting text.
                    response_text, tools_called, content_list, has_thinking = (
                        self._extract_content_blocks(final_msg.content)
                    )
                    if getattr(final_msg, "stop_reason", None) == "max_tokens":
                        logger.warning(
                            "Anthropic response truncated at max_tokens limit "
                            "(model=%s, output_tokens=%d)",
                            self._model,
                            final_msg.usage.output_tokens,
                        )
                        response_text, content_list = self._append_truncation_marker(
                            response_text, content_list, has_thinking
                        )

            else:
                try:
                    response_obj = self.client.messages.create(**kwargs)
                except ValueError as exc:
                    # The Anthropic SDK refuses a non-streaming request whose
                    # max_tokens could exceed its 10-minute timeout (e.g. the
                    # build brain's 64000). Transparently fall back to the
                    # streaming API and collect the final message — identical
                    # result, no caller-visible streaming required.
                    # https://github.com/anthropics/anthropic-sdk-python#long-requests
                    if "Streaming is required" not in str(exc):
                        raise
                    with self.client.messages.stream(**kwargs) as stream_obj:
                        response_obj = stream_obj.get_final_message()
                self._last_usage = (
                    response_obj.usage.input_tokens,
                    response_obj.usage.output_tokens,
                )
                if self._usage_callback is not None:
                    try:
                        self._usage_callback(
                            {
                                "provider": "anthropic",
                                "model": self._model,
                                "input_tokens": response_obj.usage.input_tokens,
                                "output_tokens": response_obj.usage.output_tokens,
                                "cache_read_tokens": getattr(
                                    response_obj.usage, "cache_read_input_tokens", 0
                                )
                                or 0,
                                "cache_write_tokens": getattr(
                                    response_obj.usage, "cache_creation_input_tokens", 0
                                )
                                or 0,
                                "request_id": getattr(response_obj, "id", None),
                            }
                        )
                    except Exception:
                        logger.debug(
                            "usage_callback raised an exception (ignored)",
                            exc_info=True,
                        )

                response_text, tools_called, content_list, has_thinking = (
                    self._extract_content_blocks(response_obj.content)
                )
                if getattr(response_obj, "stop_reason", None) == "max_tokens":
                    logger.warning(
                        "Anthropic response truncated at max_tokens limit "
                        "(model=%s, output_tokens=%d)",
                        self._model,
                        response_obj.usage.output_tokens,
                    )
                    response_text, content_list = self._append_truncation_marker(
                        response_text, content_list, has_thinking
                    )

            response: dict[str, Any] = {
                "role": "assistant",
                "content": content_list if has_thinking else response_text,
            }
            if tools_called:
                response["function_calls"] = tools_called

            return response, tools_called

        except Exception as e:
            self._last_usage = None
            raise ProviderError(f"Error calling Anthropic API: {e!s}") from e

    def get_token_usage(self) -> TokenUsage:
        """Get token usage from the last Anthropic API call."""
        if self._last_usage:
            return TokenUsage(
                input_tokens=self._last_usage[0],
                output_tokens=self._last_usage[1],
                provider="anthropic",
            )
        return TokenUsage(
            input_tokens=0, output_tokens=0, provider="anthropic (no data)"
        )
