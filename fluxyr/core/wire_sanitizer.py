"""wire_sanitizer — make a saved conversation replayable on a different wire.

When a user switches a session from Claude to an OpenRouter model (or back), the
stored conversation is still shaped for the wire that produced it. Replaying it
unchanged produces a 400 from the new provider, which surfaces to the user as a
generic error on a conversation that was working a moment earlier.

What actually breaks
--------------------
``AnthropicAdapter`` stores an assistant turn as ``content_list if has_thinking
else response_text`` — so when thinking is on, ``content`` is a LIST of typed
blocks: ``thinking``, ``redacted_thinking``, ``text`` and ``tool_use``.
``openai_message_formatter`` passes an assistant ``content`` list straight
through alongside its own ``tool_calls`` array. So a cross-wire switch would post
Anthropic-only block shapes to a Chat Completions endpoint.

``tool_use`` matters as much as the thinking blocks. An earlier reading of this
problem covered only ``thinking`` / ``redacted_thinking``; stripping those alone
still leaves ``tool_use`` entries in the list and still fails.

The reverse direction is not free either
----------------------------------------
Going OpenRouter → Anthropic with thinking enabled, prior assistant turns carry
no signed thinking blocks, and Anthropic rejects adaptive thinking combined with
tool use when those signatures are absent. Flattening to text is what makes the
history safe in that direction too.

The approach: flatten, don't filter
------------------------------------
Assistant content lists collapse to their plain text. Reasoning is lost, which is
correct — reasoning is model-specific and a different model's reasoning is not
evidence for the new one. The visible transcript is preserved.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Block types that exist only on the Anthropic wire.
_ANTHROPIC_ONLY_BLOCKS = frozenset({"thinking", "redacted_thinking", "tool_use"})


def flatten_content(content: Any) -> Any:
    """Collapse an Anthropic block list into plain text.

    Args:
        content: An assistant message's ``content`` — a string on the simple
                 path, or a list of typed blocks when thinking was enabled.

    Returns:
        The original value when it is already a string, else the concatenated
        text of its ``text`` blocks.
    """
    if not isinstance(content, list):
        return content

    parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "".join(parts)


def _needs_flattening(content: Any) -> bool:
    """True when ``content`` carries a block only one wire understands."""
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict) and block.get("type") in _ANTHROPIC_ONLY_BLOCKS
        for block in content
    )


def sanitize_messages(messages: list) -> tuple[list, int]:
    """Return ``(messages, flattened_count)`` safe to replay on any wire."""
    out = []
    flattened = 0

    for message in messages:
        if not isinstance(message, dict):
            out.append(message)
            continue
        if message.get("role") != "assistant":
            out.append(message)
            continue
        if not _needs_flattening(message.get("content")):
            out.append(message)
            continue

        out.append({**message, "content": flatten_content(message["content"])})
        flattened += 1

    return out, flattened


def sanitize_state_for_wire(
    state: dict[str, Any], *, target_provider: str
) -> dict[str, Any]:
    """Return a copy of ``state`` whose conversation replays on ``target_provider``.

    Args:
        state:           A saved brain state dict.
        target_provider: The provider the brain is switching TO.

    Returns:
        A shallow copy with short-term messages flattened. The input is not
        mutated — the caller may still need the original for diagnostics.
    """
    short_term = state.get("short_term") or {}
    # ShortTermMemory.to_dict serialises the turn list under "items".
    messages = short_term.get("items")
    if not isinstance(messages, list):
        return state

    sanitized, flattened = sanitize_messages(messages)
    if not flattened:
        return state

    logger.info(
        "wire_sanitizer: flattened %d assistant turn(s) for %s",
        flattened,
        target_provider,
    )
    return {
        **state,
        "short_term": {**short_term, "items": sanitized},
    }
