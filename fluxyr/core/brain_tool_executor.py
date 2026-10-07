"""Tool schema helpers and executor for SyntheticBrain.

The concurrent-batch runner lives in the sibling module ``brain_tool_batch.py``
and the ``TOOL_CALL_ID_KWARG`` constant in ``tool_call_kwargs.py`` — both split
out to keep this file under the project's file-size guideline.
"""

from collections.abc import Callable
from typing import Any

from fluxyr.core.tool_call_kwargs import TOOL_CALL_ID_KWARG, TOOL_COORD_KWARG
from fluxyr.core.tools.tool_response import ToolResponse
from fluxyr.logging import arg_keys, get_logger, result_bytes, timed

# structlog for `ai.tool.finished` / `ai.tool.failed`. This function — not the
# bridge — is where every provider's tool call lands WITH its `call_id`: the
# bridge builds a plain callable that never sees one, and this is the only
# place that has the name, the id, the arguments, the result and the mode in
# the same frame.
log = get_logger(__name__)

# The tool ARGUMENTS and RESULT never appear in a log field — only which
# argument NAMES arrived (the call's shape) and how big the answer was.


def _is_error(result: Any) -> bool:
    """True when a tool answered with the structured error shape."""
    return isinstance(result, dict) and "error" in result


# Names of built-in memory tools that the model may try to call but that are
# handled automatically — these are intercepted before reaching user-supplied tools.
_AUTO_MEMORY_TOOLS = frozenset(
    {
        "save_in_memory",
        "store_memory",
        "observe_pattern",
        "store_semantic_memory",
        "store_episodic_memory",
    }
)


def build_memory_tool(save_fn: Callable) -> dict[str, Any]:
    """Return the ``save_in_memory`` tool definition bound to ``save_fn``."""
    return {
        "name": "save_in_memory",
        "type": "function",
        "description": "Save important information to long-term memory",
        "parameters": {
            "type": "object",
            "properties": {
                "memory_type": {
                    "type": "string",
                    "enum": ["semantic", "episodic"],
                    "description": "Type of memory to save to",
                },
                "key": {
                    "type": "string",
                    "description": "Identifier for semantic memory, optional for episodic",
                },
                "content": {
                    "type": "string",
                    "description": "The information to store",
                },
                "importance": {
                    "type": "number",
                    "description": "Priority level (0.0 to 1.0, higher values indicate higher importance)",
                    "minimum": 0,
                    "maximum": 1,
                    "default": 0.5,
                },
                "metadata": {
                    "type": "object",
                    "description": "Additional metadata about this memory",
                },
            },
            "required": ["memory_type", "content"],
        },
        "function": save_fn,
    }


def build_observe_tool(observe_fn: Callable) -> dict[str, Any]:
    """Return the ``observe_pattern`` tool definition bound to ``observe_fn``."""
    return {
        "name": "observe_pattern",
        "type": "function",
        "description": "Record an observation about user behavior or preferences for implicit learning",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern_key": {
                    "type": "string",
                    "description": "The pattern category (e.g., 'communication_style', 'preferences')",
                },
                "observation": {
                    "type": "string",
                    "description": "The specific observation to record",
                },
            },
            "required": ["pattern_key", "observation"],
        },
        "function": observe_fn,
    }


def tool_begin_event(tool_call: dict[str, Any], index: int) -> dict[str, Any]:
    """Build the ``tool_begin`` lifecycle event dict for *tool_call*.

    Shared by the sequential path (``brain_tool_dispatch.run_tools_sequentially``)
    and the concurrent path (``brain_tool_batch.run_one``) so the payload the
    lifecycle callback sees never drifts between the two.

    ``index`` is the tool call's position across the WHOLE turn, not just this
    batch: a turn with multiple ``step()`` iterations passes a running
    ``base_index`` into both dispatchers so indices keep increasing (0,1,2,3)
    across iterations instead of restarting at 0 per batch. Both execution
    paths preserve position within a batch, so a listener
    (``ai_chat_helpers.run_brain_step``'s lifecycle callback) can recover the
    ORIGINAL call order even when events themselves arrive out of order —
    which happens under concurrent execution, where completion order is not
    call order — and even across iterations.
    """
    return {
        "event": "tool_begin",
        "tool_call_id": tool_call.get("call_id"),
        "tool_name": tool_call.get("name"),
        "args": tool_call.get("arguments", {}),
        "index": index,
    }


def tool_end_event(
    tool_call: dict[str, Any], response: ToolResponse, index: int
) -> dict[str, Any]:
    """Build the ``tool_end`` lifecycle event dict for *tool_call* and its *response*.

    See :func:`tool_begin_event` for why ``index`` is carried.
    """
    return {
        "event": "tool_end",
        "tool_call_id": tool_call.get("call_id"),
        "tool_name": tool_call.get("name"),
        "result": response.result,
        "mode": response.mode,
        "index": index,
    }


def execute_tool(
    tool_call: dict[str, Any], tools: list[dict[str, Any]]
) -> ToolResponse:
    """Execute a tool by looking it up in ``tools`` and calling its function.

    Memory-related tool names are intercepted and returned immediately with a
    success stub so that the model does not attempt to bypass the automatic
    memory layer.

    ``call_id`` is forwarded under ``TOOL_CALL_ID_KWARG``, and the effect-
    ledger coordinate — ``tool_call``'s optional ``coord``/``occurrence_hint``
    keys, when a dispatcher sets them — under ``TOOL_COORD_KWARG`` as a
    ``(coord, occurrence_hint)`` 2-tuple, or ``None`` when no coordinate was
    given (``tool_call_kwargs`` defines both; ``brain_tool_effect_wrap.
    build_call_ctx`` consumes them).

    Args:
        tool_call: Dict with keys ``name``, ``call_id``, ``arguments``, and
            optionally ``coord``/``occurrence_hint`` (work item C, #870).
        tools: Full tool list (including ``function`` callables).

    Returns:
        A :class:`ToolResponse`. Always — a tool with no matching entry in
        ``tools`` falls through to the trailing ``unknown_tool`` result below,
        so there is no ``None`` case for callers to guard against.
    """
    tool_name = tool_call.get("name", "")
    call_id = tool_call.get("call_id", "")
    arguments = tool_call.get("arguments", {})

    for tool in tools:
        if tool.get("name") != tool_name:
            continue

        if "function" in tool:
            keys = arg_keys(arguments)
            call = timed()
            # Reserved kwargs — see synthetic_brain.tool_call_kwargs. `coord`'s
            # producer is `tool_call_kwargs.stamp_effect_coords`, called from
            # brain.py's step() before either dispatcher runs; tool_call.get(...)
            # returns None only for a non-`irreversible` call, which that
            # function deliberately leaves unstamped.
            coord = tool_call.get("coord")
            coord_kwarg = (
                (coord, tool_call.get("occurrence_hint")) if coord is not None else None
            )
            try:
                reserved = (
                    {}
                    if tool_name in _AUTO_MEMORY_TOOLS
                    else {
                        TOOL_CALL_ID_KWARG: call_id or None,
                        TOOL_COORD_KWARG: coord_kwarg,
                    }
                )
                result, mode = tool["function"](**arguments, **reserved)
                failed = _is_error(result)
                log.info(
                    "ai.tool.finished",
                    tool_name=tool_name,
                    tool_call_id=call_id,
                    duration_ms=call.ms,
                    ok=not failed,
                    result_status="error" if failed else "ok",
                    result_bytes=result_bytes(result),
                    mode=mode,
                    arg_keys=keys.get("arg_keys", []),
                    arg_count=keys.get("arg_count", 0),
                )
                return ToolResponse(
                    tool_name=tool_name,
                    call_id=call_id,
                    result=result,
                    mode=mode,
                )
            except Exception as exc:
                # WARNING, not error: a tool that raised is reported back to
                # the brain, which usually retries or explains it — the turn is
                # degraded, not lost. `arg_keys` is the argument SHAPE the tool
                # broke on, which is what makes the league table actionable
                # without a single argument VALUE reaching the log.
                #
                # `.warning(exc_info=True)` rather than `.exception(...)`:
                # `.exception` IS error level, so the row would have been
                # registered `warning` and shipped to Sentry as an error on
                # every tool that raised. The traceback still rides along.
                log.warning(
                    "ai.tool.failed",
                    exc_info=True,
                    tool_name=tool_name,
                    tool_call_id=call_id,
                    duration_ms=call.ms,
                    error_type=type(exc).__name__,
                    arg_keys=keys.get("arg_keys", []),
                    arg_count=keys.get("arg_count", 0),
                )
                return ToolResponse(
                    tool_name=tool_name,
                    call_id=call_id,
                    result={"error": str(exc), "type": "execution_error"},
                    mode="continue",
                )
        else:
            return ToolResponse(
                tool_name=tool_name,
                call_id=call_id,
                result={
                    "error": "No function implementation for this tool",
                    "type": "missing_function",
                },
                mode="wait",
            )

    return ToolResponse(
        tool_name=tool_name,
        call_id=call_id,
        result={"error": f"Tool '{tool_name}' not found", "type": "unknown_tool"},
        mode="continue",
    )


def is_parallel_eligible(
    tool_call: dict[str, Any], tools: list[dict[str, Any]]
) -> bool:
    """Return True iff the tool call may run concurrently (``parallel_safe`` is True).

    Uses an explicit opt-in allowlist: only tools that are genuinely read-only
    and can NEVER park (never emit ``__pua__`` / ``mode="wait"``) should set
    ``parallel_safe=True``.  A missing or False value means the tool takes the
    unchanged sequential path, which covers file tools, workbench tools,
    user tools, and any future tool that may mutate state or emit a PUA.

    Args:
        tool_call: A single tool call dict (keys: ``name``, ``call_id``, ``arguments``).
        tools: Full tool list with ``parallel_safe`` flags.

    Returns:
        True only when the corresponding tool dict exists and explicitly sets
        ``parallel_safe`` to ``True``.
    """
    tool_name = tool_call.get("name", "")
    for tool in tools:
        if tool.get("name") == tool_name:
            return tool.get("parallel_safe") is True
    # Tool not found — conservative default.
    return False
