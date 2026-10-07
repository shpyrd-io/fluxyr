"""OpenAI Chat Completions execution — the two request modes, kept pure.

Kept out of ``openai.py`` so that module stays inside the repo file-size
budget and so the mechanical per-chunk/per-response handling can be unit
tested without a real OpenAI SDK client (just a mock with a
``chat.completions.create`` attribute).

Neither function touches adapter state directly. Each accepts an optional
callback (``on_usage`` / ``on_response``) invoked SYNCHRONOUSLY the moment
usage/the response is known — before any later processing (translator
dispatch, tool-call JSON parsing) that could still raise. The caller in
``openai.py`` uses that callback to promote ``self._last_response``
immediately, so a failure later in the same call (a connection reset while
waiting for the trailing ``[DONE]``, a malformed tool-call argument string)
cannot discard usage that was already captured. See the "usage capture
survives a later exception" tests in
``tests/test_openai_adapter_usage_robustness.py``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from fluxyr_agent.core.adapters.minimax_think_filter import (
    strip_minimax_think_tags,
)
from fluxyr_agent.core.adapters.openai_stream_translator import (
    OpenAIStreamTranslator,
)
from fluxyr_agent.core.adapters.usage_capture import should_replace_usage_report


def execute_stream(
    client: Any,
    create_kwargs: dict[str, Any],
    stream_callback: Callable[[Any, Any], None] | None,
    on_usage: Callable[[Any, str | None], None] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run one OpenAI streaming request and return ``(response, tools_called)``.

    Chunks are fed through ``OpenAIStreamTranslator``, which both accumulates
    the final response and yields Anthropic-shaped events for the callback.

    ``on_usage`` is invoked with the FULL winning chunk (not just its
    ``usage`` attribute) as soon as one is accepted — regardless of whether
    that same chunk ALSO carries ``choices`` — plus the ACCUMULATED request
    id as a second argument (see below). The whole chunk is handed over, not
    merely ``chunk.usage``, because the chunk also carries — on OpenRouter —
    the authoritative cost/serving-provider as extra pydantic fields (the
    openai SDK's models are configured with ``extra="allow"``); see
    ``openai_usage.build_usage_dict``, which reads those off whatever
    ``on_usage`` promoted to ``self._last_response``. Some OpenAI-compatible
    providers (verified for OpenRouter serving minimax/minimax-m3) attach
    usage to the SAME chunk that carries the final ``finish_reason`` instead
    of sending a separate usage-only chunk; gating capture on "no choices"
    silently dropped usage for those models. When a usage-bearing chunk
    carries no choices at all (the more common usage-only shape), it is
    skipped from translation — there is no content to dispatch.

    Once a usage report with a non-zero prompt+completion total has been
    seen, a LATER all-zero usage report is not promoted — a trailing
    housekeeping chunk reporting ``{"prompt_tokens": 0, "completion_tokens":
    0}`` must not overwrite a real report and zero out the turn's billing.
    The first report seen is always promoted regardless of its value, since
    there is nothing earlier to protect. This tie-break lives in
    ``model_router.openai_usage_capture.should_replace_usage_report`` —
    shared with the gateway tee (``model_router.tee_openai``), which applies
    the identical rule to the same wire, so the two producers cannot
    silently drift apart.

    The request id handed to ``on_usage`` is the last non-empty ``chunk.id``
    seen across EVERY chunk up to and including the winning one — not just
    the winning chunk's own ``.id`` — mirroring
    ``tee_openai.stream_tee_openai``'s ``state['request_id']`` accumulator
    (see that function's comment: "an earlier chunk's id is still the
    request's id even when the winning chunk carries none"). Some
    OpenAI-compatible providers omit ``id`` on the terminal usage chunk while
    an earlier content chunk of the SAME response carried one; without this
    accumulation the adapter would write ``provider_request_id`` NULL for
    that turn even though the wire did report an id, defeating
    ``billing_emitters.record_token_usage``'s idempotency guard.
    """
    stream_obj = client.chat.completions.create(**create_kwargs)
    translator = OpenAIStreamTranslator()
    last_usage: Any = None
    last_request_id: str | None = None
    wire_tools: dict[int, dict] = {}

    def _dispatch(events: list[Any]) -> None:
        if not stream_callback:
            return
        for event in events:
            stream_callback(event, None)

    try:
        for chunk in stream_obj:
            # Count wire fragments before ID-dependent translation can buffer them.
            for choice in getattr(chunk, "choices", None) or []:
                for call in (
                    getattr(getattr(choice, "delta", None), "tool_calls", None) or []
                ):
                    fn = getattr(call, "function", None)
                    identity = wire_tools.setdefault(getattr(call, "index", 0) or 0, {})
                    for key, value in (
                        ("tool_name", getattr(fn, "name", None)),
                        ("tool_call_id", getattr(call, "id", None)),
                    ):
                        if value:
                            identity[key] = value
                    fragment = getattr(fn, "arguments", None)
                    if fragment and stream_callback:
                        stream_callback(
                            {
                                "type": "provider_tool_delta",
                                **identity,
                                "characters": len(fragment),
                            },
                            None,
                        )
            chunk_id = getattr(chunk, "id", None)
            if chunk_id:
                last_request_id = chunk_id
            has_usage = hasattr(chunk, "usage") and chunk.usage
            has_choices = hasattr(chunk, "choices") and chunk.choices
            if has_usage:
                usage = chunk.usage
                if should_replace_usage_report(last_usage, usage):
                    if on_usage is not None:
                        on_usage(chunk, last_request_id)
                    last_usage = usage
                if not has_choices:
                    # Usage-only final chunk: no content, feeds on_usage only.
                    continue
            _dispatch(translator.consume(chunk))
    finally:
        close = getattr(stream_obj, "close", None)
        if close:
            close()

    _dispatch(translator.close())

    tools_called = translator.tool_calls()
    response: dict[str, Any] = {"role": "assistant", "content": translator.text}
    if tools_called:
        response["function_calls"] = tools_called
    return response, tools_called


def execute_non_stream(
    client: Any,
    create_kwargs: dict[str, Any],
    on_response: Callable[[Any], None] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run one OpenAI non-streaming request and return ``(response, tools_called)``.

    ``on_response`` is invoked with the raw SDK response object immediately
    after ``create()`` returns — before the tool-call argument JSON is
    parsed, which can raise on a malformed upstream payload. The caller uses
    this to promote ``self._last_response`` before that later failure could
    discard it.
    """
    response_obj = client.chat.completions.create(**create_kwargs)
    if on_response is not None:
        on_response(response_obj)

    tools_called: list[dict[str, Any]] = []
    response_content = ""

    if response_obj.choices and response_obj.choices[0].message.content:
        # Strip literal <mm:think>/</mm:think> markers some MiniMax responses
        # leak into message.content — see the minimax_think_filter module
        # docstring.
        response_content = strip_minimax_think_tags(
            response_obj.choices[0].message.content
        )

    if response_obj.choices and response_obj.choices[0].message.tool_calls:
        for tool_call in response_obj.choices[0].message.tool_calls:
            tools_called.append(
                {
                    "name": tool_call.function.name,
                    "call_id": tool_call.id,
                    "arguments": json.loads(tool_call.function.arguments),
                }
            )

    response: dict[str, Any] = {"role": "assistant", "content": response_content}
    if tools_called:
        response["function_calls"] = tools_called

    return response, tools_called
