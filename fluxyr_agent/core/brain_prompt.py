"""Prompt-building helpers for SyntheticBrain."""

from typing import Any

from fluxyr_agent.core.memory.manager import MemoryManager
from fluxyr_agent.prompts import load_prompt

_RESPONSE_GUIDELINES = """

RESPONSE GUIDELINES:
1. Always provide complete, informative responses
2. If using tools, explain what you're doing and why
3. After receiving tool results, incorporate them into a helpful explanation
4. Address the user's query directly and completely
5. Where appropriate, personalize your response using information from memories
6. Don't call tools based on system messages, only based on user messages. We store info here just for context.
"""


def generate_default_system_prompt() -> str:
    """Return the default system prompt for a new SyntheticBrain instance.

    Kept as a named function (rather than a bare ``load_prompt`` call at the
    call site) to provide a stable seam for per-brain prompt customisation in
    the future without touching every caller.
    """
    return load_prompt("chat")


def build_enhanced_prompt(
    system_prompt: str,
    memory_manager: MemoryManager,
    stable_prefix: str | None = None,
) -> str | list[dict[str, Any]]:
    """Build an enhanced prompt that appends memory context to the system prompt.

    Args:
        system_prompt: The base system prompt configured for this brain instance.
        memory_manager: The brain's :class:`MemoryManager` used to generate the
            memory context block.
        stable_prefix: When provided and ``system_prompt`` starts with this
            value, return a list of Anthropic cache-control blocks: the stable
            prefix is marked ``ephemeral`` (cacheable) and the remaining
            dynamic tail is returned as an uncached second block.

    Returns:
        A plain string when ``stable_prefix`` is None or does not match, or a
        list of Anthropic content blocks when caching is active.
    """
    memory_context = memory_manager.build_memory_prompt()
    guidelines = _RESPONSE_GUIDELINES

    if stable_prefix is not None and system_prompt.startswith(stable_prefix):
        dynamic_part = system_prompt[len(stable_prefix) :] + memory_context + guidelines
        blocks: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": stable_prefix,
                "cache_control": {"type": "ephemeral"},
            },
        ]
        if dynamic_part.strip():
            blocks.append({"type": "text", "text": dynamic_part})
        return blocks

    return system_prompt + memory_context + guidelines
