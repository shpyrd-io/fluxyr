"""Translate OpenAI-wire stream chunks into Anthropic-shaped stream events.

Every stream consumer in this codebase dispatches on Anthropic SDK event shapes
(``event.type``, ``event.index``, ``event.content_block``, ``event.delta``) — see
``ai_chat_helpers.make_stream_callback``, ``skill_build_service`` and
``skill_rebuild_service``.  An OpenAI ``ChatCompletionChunk`` carries none of
those attributes, so forwarding a raw chunk matches no branch and renders
nothing.

``OpenAIStreamTranslator`` sits between the two wires: it consumes OpenAI chunks
and returns synthetic events that quack like the Anthropic SDK ones, while also
accumulating the final assistant text and tool calls so the adapter does not
parse the same chunk twice.

Synthetic block indices
-----------------------
OpenAI has no content-block concept, and ``tool_call.index`` indexes the
*tool-calls array*, not content blocks.  The translator therefore keeps its own
monotonically increasing block counter and maps each channel — text, reasoning,
and each tool-call array index — onto its own synthetic block index.

Block lifecycle
---------------
OpenAI has no ``content_block_stop`` analogue, only ``finish_reason``.  A block
is closed when a different channel takes over (reasoning ends when text or a
tool call starts; text ends when a tool call starts) and every still-open block
is closed on ``finish_reason`` or at end of stream.  ``ai_chat_helpers`` needs
the matching ``content_block_stop`` or a thinking block never closes in the UI.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any

from fluxyr.core.adapters.minimax_think_filter import (
    MinimaxThinkTagStripper,
)

logger = logging.getLogger(__name__)


def _block_start(index: int, content_block: Any) -> Any:
    return SimpleNamespace(
        type="content_block_start", index=index, content_block=content_block
    )


def _block_delta(index: int, delta: Any) -> Any:
    return SimpleNamespace(type="content_block_delta", index=index, delta=delta)


def _block_stop(index: int) -> Any:
    return SimpleNamespace(type="content_block_stop", index=index)


def extract_reasoning_text(delta: Any) -> str:
    """Return the reasoning text carried by an OpenAI delta, or ''.

    OpenRouter streams reasoning either as a plain ``delta.reasoning`` string or
    as ``delta.reasoning_details`` — a list of dicts/objects whose text lives
    under ``text`` (or ``summary`` for summarised reasoning).  Both are handled;
    neither is guaranteed to be present.
    """
    reasoning = getattr(delta, "reasoning", None)
    if isinstance(reasoning, str) and reasoning:
        return reasoning

    details = getattr(delta, "reasoning_details", None)
    if not details:
        return ""

    parts: list[str] = []
    for item in details:
        if isinstance(item, dict):
            value = item.get("text") or item.get("summary")
        else:
            value = getattr(item, "text", None) or getattr(item, "summary", None)
        if isinstance(value, str) and value:
            parts.append(value)
    return "".join(parts)


class OpenAIStreamTranslator:
    """Stateful OpenAI-chunk → Anthropic-event translator and accumulator.

    One instance handles exactly one stream.  Call :meth:`consume` for every
    chunk and :meth:`close` once the stream is exhausted; both return a list of
    synthetic events to hand to the stream callback.  After the stream ends,
    :attr:`text` and :meth:`tool_calls` hold the accumulated response.
    """

    def __init__(self) -> None:
        self._next_index = 0
        self._open_indices: list[int] = []
        self._text_index: int | None = None
        self._thinking_index: int | None = None
        # tool_call.index (tool-calls array) → synthetic content-block index.
        self._tool_block_index: dict[int, int] = {}
        # Argument fragments seen before the tool-call id arrived.
        self._pending_arguments: dict[int, str] = {}
        self._text_parts: list[str] = []
        self._tool_entries: dict[int, dict[str, Any]] = {}
        self._mm_think_filter = MinimaxThinkTagStripper()

    # -- accumulated response -------------------------------------------------

    @property
    def text(self) -> str:
        """Concatenated assistant text seen so far."""
        return "".join(self._text_parts)

    def tool_calls(self) -> list[dict[str, Any]]:
        """Return the completed tool calls in tool-call-array order.

        Entries missing an id or a name, or whose accumulated arguments are not
        valid JSON, are logged and skipped — a partial tool call cannot be
        executed.
        """
        calls: list[dict[str, Any]] = []
        for call_index in sorted(self._tool_entries):
            entry = self._tool_entries[call_index]
            if not entry["id"] or not entry["name"]:
                continue
            args_str = entry["arguments"]
            try:
                arguments = json.loads(args_str)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "Failed to parse tool call arguments: %s, error: %s",
                    args_str,
                    str(exc),
                )
                continue
            calls.append(
                {
                    "name": entry["name"],
                    "call_id": entry["id"],
                    "arguments": arguments,
                }
            )
        return calls

    # -- translation ----------------------------------------------------------

    def consume(self, chunk: Any) -> list[Any]:
        """Translate one OpenAI chunk into zero or more Anthropic-shaped events.

        A chunk with no choices (the usage-only final chunk) carries no content
        and yields no events — that chunk feeds the usage callback instead.
        """
        choices = getattr(chunk, "choices", None)
        if not choices:
            return []

        events: list[Any] = []
        choice = choices[0]
        delta = getattr(choice, "delta", None)

        if delta is not None:
            reasoning_text = extract_reasoning_text(delta)
            if reasoning_text:
                events.extend(self._on_reasoning(reasoning_text))

            content = getattr(delta, "content", None)
            if content:
                # Strip literal <mm:think>/</mm:think> markers before treating
                # this as assistant text — see MinimaxThinkTagStripper. A chunk
                # that is entirely a tag marker (or a held-back partial one)
                # filters to '' and is dropped rather than opening a text
                # block for zero real characters.
                filtered_content = self._mm_think_filter.feed(content)
                if filtered_content:
                    events.extend(self._on_text(filtered_content))

            tool_calls = getattr(delta, "tool_calls", None)
            if tool_calls:
                for tool_call in tool_calls:
                    events.extend(self._on_tool_call(tool_call))

        if getattr(choice, "finish_reason", None):
            events.extend(self.close())
        return events

    def close(self) -> list[Any]:
        """Close every still-open block, oldest first.  Idempotent.

        Flushes the mm:think carry buffer first — a fragment held back as a
        possible partial tag that the stream ended without completing was
        real text all along, so it is emitted as a trailing text delta before
        the blocks it belongs to are closed.
        """
        events: list[Any] = []
        remaining = self._mm_think_filter.flush()
        if remaining:
            events.extend(self._on_text(remaining))
        events.extend(_block_stop(index) for index in self._open_indices)
        self._open_indices = []
        self._text_index = None
        self._thinking_index = None
        return events

    # -- internals ------------------------------------------------------------

    def _open_block(self, content_block: Any) -> tuple[int, Any]:
        index = self._next_index
        self._next_index += 1
        self._open_indices.append(index)
        return index, _block_start(index, content_block)

    def _close_index(self, index: int) -> list[Any]:
        if index not in self._open_indices:
            return []
        self._open_indices.remove(index)
        return [_block_stop(index)]

    def _close_thinking(self) -> list[Any]:
        if self._thinking_index is None:
            return []
        index, self._thinking_index = self._thinking_index, None
        return self._close_index(index)

    def _close_text(self) -> list[Any]:
        if self._text_index is None:
            return []
        index, self._text_index = self._text_index, None
        return self._close_index(index)

    def _on_reasoning(self, text: str) -> list[Any]:
        events: list[Any] = []
        if self._thinking_index is None:
            self._thinking_index, start = self._open_block(
                SimpleNamespace(type="thinking", thinking="")
            )
            events.append(start)
        events.append(
            _block_delta(
                self._thinking_index,
                SimpleNamespace(type="thinking_delta", thinking=text),
            )
        )
        return events

    def _on_text(self, content: str) -> list[Any]:
        events = self._close_thinking()
        if self._text_index is None:
            self._text_index, start = self._open_block(
                SimpleNamespace(type="text", text="")
            )
            events.append(start)
        events.append(
            _block_delta(
                self._text_index, SimpleNamespace(type="text_delta", text=content)
            )
        )
        self._text_parts.append(content)
        return events

    def _on_tool_call(self, tool_call: Any) -> list[Any]:
        events = self._close_thinking()
        events.extend(self._close_text())

        call_index = getattr(tool_call, "index", 0) or 0
        entry = self._tool_entries.setdefault(
            call_index, {"id": None, "name": None, "arguments": ""}
        )

        call_id = getattr(tool_call, "id", None)
        if call_id:
            entry["id"] = call_id

        function = getattr(tool_call, "function", None)
        name = getattr(function, "name", None) if function is not None else None
        if name:
            entry["name"] = name
        arguments = (
            getattr(function, "arguments", None) if function is not None else None
        )
        if arguments:
            entry["arguments"] += arguments

        # OpenAI sends the id and function.name only on the FIRST delta of a
        # tool call; open the synthetic block as soon as the id shows up.
        if call_index not in self._tool_block_index and entry["id"]:
            block_index, start = self._open_block(
                SimpleNamespace(
                    type="tool_use", id=entry["id"], name=entry["name"] or "", input={}
                )
            )
            self._tool_block_index[call_index] = block_index
            events.append(start)
            buffered = self._pending_arguments.pop(call_index, "")
            if buffered:
                events.append(
                    _block_delta(
                        block_index,
                        SimpleNamespace(type="input_json_delta", partial_json=buffered),
                    )
                )

        if arguments:
            block_index = self._tool_block_index.get(call_index)
            if block_index is None:
                self._pending_arguments[call_index] = (
                    self._pending_arguments.get(call_index, "") + arguments
                )
            else:
                events.append(
                    _block_delta(
                        block_index,
                        SimpleNamespace(
                            type="input_json_delta", partial_json=arguments
                        ),
                    )
                )
        return events
