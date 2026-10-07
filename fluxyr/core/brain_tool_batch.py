"""Concurrent tool-batch runner for SyntheticBrain.

Split out of ``brain_tool_executor.py`` (which owns the single-call
``execute_tool``) to keep both files under the project's file-size
guideline.
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from flask import current_app

from fluxyr.core.brain_tool_executor import (
    execute_tool,
    tool_begin_event,
    tool_end_event,
)
from fluxyr.core.tools.tool_response import ToolResponse
from fluxyr.logging import carry_context, get_logger

# Default (and fallback, outside an app context) maximum worker threads used
# for a concurrent tool batch.
log = get_logger(__name__)

MAX_CONCURRENT_WORKERS = 6


def _max_concurrent_workers() -> int:
    """Return the configured worker cap for a concurrent tool batch.

    Reads BRAIN_TOOL_BATCH_MAX_WORKERS from Flask config so it can be tuned
    per environment (techdoc fans out further internally than other tools).
    Falls back to MAX_CONCURRENT_WORKERS when no application context is
    active (e.g. a unit test that calls this module directly).

    Clamped to a floor of 1 — ThreadPoolExecutor(max_workers=0) raises
    ValueError, so a misconfigured 0 or negative override must not crash
    every multi-tool turn.
    """
    try:
        configured = current_app.config.get(
            "BRAIN_TOOL_BATCH_MAX_WORKERS", MAX_CONCURRENT_WORKERS
        )
    except RuntimeError:
        return MAX_CONCURRENT_WORKERS
    return max(1, int(configured))


def execute_tool_batch_concurrent(
    tools_called: list[dict[str, Any]],
    all_tools: list[dict[str, Any]],
    lifecycle_callback: Callable[[dict[str, Any]], None] | None,
    base_index: int = 0,
) -> tuple[list[ToolResponse], bool]:
    """Run all tools in *tools_called* concurrently and return results in original order.

    The lifecycle callbacks (tool_begin / tool_end) run **inside each worker
    thread** so that any thread-local state set by the callback (e.g. the active
    tool_call_id used to tag progress events) is correctly scoped to the thread
    that executes the tool.

    After all futures finish, results are collected in the original
    ``tools_called`` order for deterministic short-term memory appends.

    Safety guard: if any result carries ``mode="wait"``, the caller must park
    the session.  This function signals that by returning ``(results, True)``
    (second element is ``has_wait``).  Results are still ordered and complete so
    the caller can apply the same parking logic as the sequential path.

    Args:
        tools_called: Ordered list of tool call dicts for this turn.
        all_tools: Full tool list with callables.
        lifecycle_callback: Optional brain lifecycle hook (tool_begin / tool_end).
        base_index: Running count of tool calls completed in EARLIER iterations
            of the same ``step()`` call. The lifecycle event's ``index`` is
            ``base_index`` plus this batch's local position, so a multi-iteration
            turn gets turn-global, monotonically increasing indices (0,1,2,3 —
            never a restart to 0 per batch). ``ai_chat_helpers``'s legacy
            fallback sorts a turn's whole ``completed_tool_calls`` list by this
            value, so a batch-local index would interleave tool blocks from
            different iterations.

    Returns:
        Tuple of (ordered_responses, has_wait) where ``has_wait`` is True when
        at least one result has ``mode="wait"``.
    """
    if not tools_called:
        # ThreadPoolExecutor(max_workers=0) raises ValueError — an empty batch
        # is a legitimate (if unusual) caller state, not an error.
        return [], False

    workers = min(len(tools_called), _max_concurrent_workers())

    def run_one(idx: int, tool_call: dict[str, Any]) -> tuple[int, ToolResponse]:
        if lifecycle_callback:
            lifecycle_callback(tool_begin_event(tool_call, base_index + idx))
        response = execute_tool(tool_call, all_tools)
        if lifecycle_callback:
            lifecycle_callback(tool_end_event(tool_call, response, base_index + idx))
        return idx, response

    # execute_tool never returns None (see its docstring), so every index
    # submitted below is guaranteed to be filled in exactly once.
    results_by_index: dict[int, ToolResponse] = {}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        # carry_context: worker threads start with an EMPTY logging context
        # and pooled workers keep the previous task's binding otherwise.
        futures = [
            executor.submit(carry_context(run_one), i, tool_call)
            for i, tool_call in enumerate(tools_called)
        ]

        for future in as_completed(futures):
            idx, response = future.result()
            results_by_index[idx] = response

    ordered: list[ToolResponse] = [
        results_by_index[i] for i in range(len(tools_called))
    ]
    has_wait = any(r.mode == "wait" for r in ordered)
    # park_on_all_waits preserves completed siblings and every parked call.
    # Local actions may legitimately request human input during a batch.
    return ordered, has_wait
