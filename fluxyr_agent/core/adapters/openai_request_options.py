"""Request-option helpers for the OpenAI-compatible wire.

Kept out of ``openai.py`` so the adapter module stays inside the repo file-size
budget.  Everything here is pure: no SDK import, no I/O.
"""

from __future__ import annotations

from typing import Any

# Anthropic tool_choice types that map onto an OpenAI string literal.
_TOOL_CHOICE_LITERALS = {
    "any": "required",
    "auto": "auto",
    "none": "none",
}


def translate_tool_choice(
    tool_choice: dict[str, Any] | None,
) -> str | dict[str, Any] | None:
    """Translate an Anthropic-shaped tool_choice into the OpenAI wire shape.

    ``{'type': 'tool', 'name': 'X'}`` → ``{'type': 'function', 'function': {'name': 'X'}}``
    ``{'type': 'any'}``               → ``'required'``
    ``{'type': 'auto'}``              → ``'auto'``
    ``{'type': 'none'}``              → ``'none'``

    Args:
        tool_choice: Anthropic-shaped dict, an already-OpenAI-shaped string, or
            None.  None returns None so the caller omits the kwarg entirely and
            existing behaviour stays byte-identical.

    Returns:
        The OpenAI-wire tool_choice value, or None when nothing was supplied.

    Raises:
        ValueError: On an unrecognised type, or a ``tool`` choice with no name.
            Silently dropping a forced-tool choice would produce unconstrained
            output for callers that depend on it.
    """
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        # Already OpenAI-shaped ('auto' / 'required' / 'none') — pass through.
        return tool_choice

    choice_type = tool_choice.get("type")
    if choice_type == "tool":
        name = tool_choice.get("name")
        if not name:
            raise ValueError("tool_choice {'type': 'tool'} requires a 'name'")
        return {"type": "function", "function": {"name": name}}
    if choice_type in _TOOL_CHOICE_LITERALS:
        return _TOOL_CHOICE_LITERALS[choice_type]
    raise ValueError(
        f"Unsupported tool_choice type for the OpenAI wire: {choice_type!r}"
    )


def build_extra_body(
    reasoning: dict[str, Any] | None = None,
    provider_preferences: dict[str, Any] | None = None,
    include_usage_cost: bool = False,
) -> dict[str, Any] | None:
    """Build the ``extra_body`` payload for OpenRouter-only request parameters.

    The OpenAI SDK rejects unknown top-level kwargs, so OpenRouter extensions
    (``reasoning``, ``provider``) must ride in ``extra_body``.

    Args:
        reasoning: OpenRouter reasoning object, e.g. ``{'effort': 'high'}`` or
            ``{'enabled': False}``.  Falsy/None → omitted.
        provider_preferences: OpenRouter provider-routing preferences, e.g.
            ``{'require_parameters': True}``.  Falsy/None → omitted.
        include_usage_cost: When True, ask OpenRouter to report what it actually
            charged (``usage: {'include': True}``). One model id is served from
            many provider endpoints at materially different prices — up to 3.26x
            on the models we have measured — so the reported cost is the only
            reliable per-request number.

    Returns:
        The extra_body dict, or None when neither option is set — so the caller
        omits the kwarg and current behaviour is unchanged.
    """
    extra_body: dict[str, Any] = {}
    if reasoning:
        extra_body["reasoning"] = reasoning
    if provider_preferences:
        extra_body["provider"] = provider_preferences
    if include_usage_cost:
        extra_body["usage"] = {"include": True}
    return extra_body or None


def flatten_system_prompt(system_prompt: str | list[dict[str, Any]]) -> str:
    """Flatten an Anthropic structured system prompt into plain text.

    OpenAI has no structured system blocks; a cache-control block list collapses
    to newline-joined text.
    """
    if isinstance(system_prompt, list):
        return "\n".join(
            block.get("text", "") for block in system_prompt if isinstance(block, dict)
        )
    return system_prompt


def build_create_kwargs(
    *,
    model: str,
    formatted_messages: list[dict[str, Any]],
    formatted_tools: list[dict[str, Any]] | None,
    wire_tool_choice: str | dict[str, Any] | None,
    stream: bool,
    max_tokens: int | None,
    reasoning: dict[str, Any] | None,
    provider_preferences: dict[str, Any] | None,
    provider: str,
) -> dict[str, Any]:
    """Assemble the chat.completions.create() kwargs for one request.

    Optional parameters are omitted entirely when unset, so a caller that
    configures nothing gets a byte-identical request to before.
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": formatted_messages,
        "tools": formatted_tools,
    }
    if stream:
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
    if max_tokens is not None:
        kwargs["max_completion_tokens" if provider == "openai" else "max_tokens"] = (
            max_tokens
        )

    if wire_tool_choice is not None:
        kwargs["tool_choice"] = wire_tool_choice

    extra_body = build_extra_body(
        reasoning if provider == "openrouter" else None,
        provider_preferences if provider == "openrouter" else None,
        # Only OpenRouter reports what it actually charged, and only on
        # request; a plain OpenAI-compatible endpoint rejects the key.
        include_usage_cost=(provider == "openrouter"),
    )
    if reasoning and provider == "openai" and reasoning.get("effort"):
        kwargs["reasoning_effort"] = reasoning["effort"]
    if extra_body is not None:
        kwargs["extra_body"] = extra_body
    return kwargs
