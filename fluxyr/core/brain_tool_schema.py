"""Tool-schema helpers shared by ``brain_tool_bridge`` and ``brain_tool_executor``.

Split out to give both of those files headroom under the project's
file-size guideline — they were already at the 300-line ceiling before this
extraction, with no room left for this phase's own additions (the two new
``ToolDefinition`` routing flags, ``side_effecting`` and ``irreversible``).
"""

from collections.abc import Callable
from typing import Any

# Brain-internal keys that must be stripped before sending tool schemas to the
# AI provider (routing flags brain_tool_bridge.py carries in — never JSON schema).
INTERNAL_KEYS = frozenset(
    {
        "function",
        "parallel_safe",
        "requires_approval",
        "side_effecting",
        "irreversible",
        "version_id",
    }
)


def get_tool_schema(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the tool list without callable ``function`` references or runtime flags.

    The AI provider APIs accept only serialisable data, so we strip out the
    Python callable before sending the schema, along with every brain-internal
    routing flag (``parallel_safe``, ``requires_approval``, ``side_effecting``,
    ``irreversible``) — none of them are part of the JSON schema spec.

    Args:
        tools: Full tool definition list (may include a ``function`` key).

    Returns:
        List of tool dicts suitable for passing to a provider adapter.
    """
    return [{k: v for k, v in tool.items() if k not in INTERNAL_KEYS} for tool in tools]


def brain_tool_from_definition(
    tool_def,
    make_callable: Callable[[str, bool, bool], Callable],
) -> dict[str, Any]:
    """Build ``brain_tool_bridge.tools_factory``'s per-tool dict from a ``ToolDefinition``.

    Carries the routing flags ``execution_policy`` and ``is_parallel_eligible``
    read (``parallel_safe``, ``requires_approval``, ``side_effecting``,
    ``irreversible``) into the plain dict SyntheticBrain works with — split out
    so ``tools_factory`` itself does not push ``brain_tool_bridge.py`` over the
    file-size guideline.

    ``irreversible`` is passed to ``make_callable`` as a THIRD argument (not
    read back out of a name-keyed side cache) so the ledger's chokepoint
    cannot silently fail open when a tool is renamed or re-fetched between
    ``tools_factory()`` refreshes — the flag travels with the callable it
    describes.
    """
    requires_approval = tool_def.requires_approval
    return {
        "name": tool_def.name,
        "type": "function",
        "description": tool_def.description,
        "parameters": tool_def.parameters,
        "function": make_callable(
            tool_def.name, requires_approval, tool_def.irreversible
        ),
        "parallel_safe": tool_def.parallel_safe,
        "requires_approval": requires_approval,
        "side_effecting": tool_def.side_effecting,
        "irreversible": tool_def.irreversible,
    }
