"""Tool-batch dispatch decision and wait-parking logic for SyntheticBrain.step().

Split out of ``brain.py`` (concurrent-vs-sequential dispatch, sequential
execution, and wait-parking) to keep that file under the project's file-size
guideline. Every function here is a PLAIN function — none take ``self`` — so
``step()`` applies the returned state onto the brain instance itself; the
control flow and every side effect are unchanged from the pre-extraction
in-class methods.
"""

import json
from collections.abc import Callable
from typing import Any

from fluxyr.core.brain_tool_executor import (
    execute_tool,
    is_parallel_eligible,
    tool_begin_event,
    tool_end_event,
)
from fluxyr.core.tool_execution_policy import MAX_PARKED_TOOLS, execution_policy
from fluxyr.core.tools.tool_response import ToolResponse
from fluxyr.persistence import strip_volatile

# The PUA kinds a parked tool can carry (Part VII). Read off the tool's
# `__pua__` envelope, defaulting to `approval` — which is what
# `tool_lifecycle_callback` already assumes for an envelope with no type.
_PUA_KINDS = frozenset({"approval", "input", "choices"})

# A2b: per-entry / whole-list size caps for a call's `_result` before it
# enters `brain_state` (via `pending_tools`). The per-entry cap alone does
# NOT guarantee the whole-list ceiling: a batch is not limited to 8 entries
# (only the number of PARKED entries is capped by MAX_PARKED_TOOLS — the
# number of `completed`/`not_executed` siblings is unbounded), and a `parked`
# entry's own `_result` is stored verbatim and uncapped by design (it is what
# the human-action resume replays from). `park_on_all_waits` therefore
# enforces the ceiling explicitly, tracking a running total across every
# entry — parked entries count toward it (their size is real bytes sitting in
# `brain_state`) even though their own content is never rewritten.
_RESULT_CAP_BYTES = 32 * 1024
_PENDING_TOOLS_CAP_BYTES = MAX_PARKED_TOOLS * _RESULT_CAP_BYTES  # 256 KB

# A1: the synthesized result for a call the brain deliberately did not
# dispatch because an earlier call in the same batch is awaiting a user
# decision. Carries an `error` key (so brain_tool_executor._is_error and the
# model both treat it as retryable) plus `deferred: true` (so the frontend can
# render it as deferred rather than failed) — contrast with the ledger's
# replay refusal (Phase 3), which deliberately carries no `error` key because
# that path must never be retried. `__not_executed__` is a reserved marker
# `_build_tool_call_block` (ai_chat_helpers.py) keys its persistence
# short-circuit on — narrower than matching the `deferred` key alone, which an
# arbitrary user-authored tool's own output could coincidentally also carry.
_NOT_EXECUTED_RESULT: dict[str, Any] = {
    "error": "not_executed",
    "executed": False,
    "deferred": True,
    "__not_executed__": True,
    "reason": (
        "Not run: an earlier tool call in this turn is awaiting a user "
        "decision. Re-issue this call after the pending action resolves."
    ),
}


def _pua_kind(result: Any) -> str:
    """The kind of human decision a parked tool is waiting on."""
    pua = result.get("__pua__") if isinstance(result, dict) else None
    kind = pua.get("type") if isinstance(pua, dict) else None
    return kind if kind in _PUA_KINDS else "approval"


def should_run_concurrent(
    tools_called: list[dict[str, Any]],
    all_tools: list[dict[str, Any]],
) -> bool:
    """True when this turn's tool batch may run concurrently.

    Concurrent path: 2+ tools and every call is parallel-eligible.
    Sequential path: everything else (preserves parking / PUA semantics).
    """
    # Bound simultaneous human cards as well as worker count. Larger batches
    # keep the sequential path's suspension limit.
    return 2 <= len(tools_called) <= MAX_PARKED_TOOLS and all(
        is_parallel_eligible(tc, all_tools) for tc in tools_called
    )


def _json_size(value: Any) -> int:
    """Serialised size of *value* as canonical JSON, or 0 if it can't be."""
    try:
        return len(json.dumps(value, sort_keys=True, default=str))
    except (TypeError, ValueError):
        return 0


def _capped_result(result: Any, running_total: int) -> tuple[Any, int]:
    """A2b: strip volatile keys and cap a completed/not_executed entry's `_result`.

    Applied only to `completed`/`not_executed` entries — a `parked` entry's
    `_result` (the raw `__pua__`-carrying tool output) stays verbatim in
    `brain_state`, which is what a human-action resume replays from; its own size
    discipline lives at the PERSISTED-BLOCK layer instead (see
    `ai_chat_pua_persistence.strip_pua_for_persist`). A parked entry's size
    still counts toward *running_total* — see the call site.

    `brain_state` has none of `ai_chat_tool_persistence`'s slimming rules, so
    without this a result that ran before a park could sit there, raw, for the
    whole human-decision window — multiplied across every entry in the batch.

    Args:
        result: The raw tool result to strip and cap.
        running_total: Bytes already committed to `pending_tools` by earlier
            entries in this same walk (A2b's whole-list ceiling).

    Returns:
        ``(capped_result, new_running_total)``.
    """
    stripped = strip_volatile(result) if isinstance(result, dict) else result
    size = _json_size(stripped)
    if size == 0:
        # Every serialisable JSON value dumps to at least one character
        # ("0", "null", '""'), so a zero here means `_json_size` swallowed a
        # (TypeError, ValueError) — the result cannot be serialised at all.
        marker = {"error": "result_not_serializable"}
        return marker, running_total + _json_size(marker)
    if size > _RESULT_CAP_BYTES or running_total + size > _PENDING_TOOLS_CAP_BYTES:
        marker = {"error": "result_too_large_to_park", "size_bytes": size}
        return marker, running_total + _json_size(marker)
    return stripped, running_total + size


def park_on_all_waits(
    tool_responses: list[ToolResponse],
    tools_called: list[dict[str, Any]],
    suspended_indices: "frozenset[int]" = frozenset(),
) -> dict[str, Any] | None:
    """Build the N-park state for a fully-walked batch (A2).

    Unlike the pre-Phase-1 ``park_on_first_wait``, this never stops the walk
    early — both dispatchers now run (or deliberately suspend) every call in
    the batch and hand the complete, positionally-ordered result list here.
    Every entry gets a `_status` (`'parked'` | `'completed'` | `'not_executed'`
    — exactly these three values) and a `_result` (A2), so a later reload can
    never find an entry with neither.

    Args:
        tool_responses: One response per *tools_called* entry, in the same
            order (both dispatchers guarantee this — suspended entries get a
            synthesized ``ToolResponse`` too, so the two lists are always the
            same length).
        tools_called: This iteration's tool calls, in original order.
        suspended_indices: Indices (positions in *tools_called*) A1 decided
            not to dispatch. Empty on the concurrent path, which never
            suspends anything.

    Returns:
        ``None`` when no result carries ``mode="wait"`` (caller should not
        park). Otherwise a dict:
            - ``'parks'``: one ``{'pua_kind', 'tool_name', 'call_id'}`` per
              wait, in original order.
            - ``'pending_tools'``: *tools_called*, in original order, each
              entry carrying ``_status`` and ``_result``.
    """
    parks: list[dict[str, Any]] = []
    pending_tools: list[dict[str, Any]] = []
    # A2b's whole-list ceiling: bytes committed to `pending_tools` so far in
    # this walk. A `parked` entry is never itself rewritten, but its size is
    # added too — it is real bytes sitting in `brain_state` for the same
    # human-decision window, and letting it not count would let N parked
    # entries alone blow the ceiling this loop exists to enforce.
    running_total = 0

    for i, tool_call in enumerate(tools_called):
        response = tool_responses[i] if i < len(tool_responses) else None
        entry = dict(tool_call)

        if i in suspended_indices:
            entry["_status"] = "not_executed"
            raw_result = (
                response.result if response is not None else dict(_NOT_EXECUTED_RESULT)
            )
            entry["_result"], running_total = _capped_result(raw_result, running_total)
        elif response is not None and response.mode == "wait":
            entry["_status"] = "parked"
            # Verbatim — the human-action resume dispatchers replay
            # from this exact shape (see park_on_all_waits' A2b docstring).
            entry["_result"] = response.result
            running_total += _json_size(response.result)
            parks.append(
                {
                    "pua_kind": _pua_kind(response.result),
                    "tool_name": tool_call.get("name") or "unknown",
                    "call_id": tool_call.get("call_id") or "",
                }
            )
        else:
            entry["_status"] = "completed"
            # A6/round-3 amendment 6: a missing response is answered
            # explicitly, never left absent — a reader must never see `null`.
            raw_result = (
                response.result
                if response is not None
                else {
                    "error": "result_unavailable",
                    "executed": False,
                    "reason": "No result was recorded for this call.",
                }
            )
            entry["_result"], running_total = _capped_result(raw_result, running_total)

        pending_tools.append(entry)

    if not parks:
        return None
    return {"parks": parks, "pending_tools": pending_tools}


def _suspend_call(
    tool_call: dict[str, Any],
    tool_lifecycle_callback: Callable[[dict[str, Any]], None] | None,
    index: int,
) -> ToolResponse:
    """A1: emit the FULL lifecycle pair for a call the brain will not dispatch.

    Normative, not incidental (round-3 amendment 3): without both `tool_begin`
    and `tool_end`, a suspended call never lands in `completed_tool_calls`
    (``ai_chat_progress_tagging.py``), so `_emit_tool_call` finds no record,
    its block silently vanishes from the persisted turn, and its live
    `tool-call-begin` on the client never gets a matching end.
    """
    response = ToolResponse(
        tool_name=tool_call.get("name", ""),
        call_id=tool_call.get("call_id", ""),
        result=dict(_NOT_EXECUTED_RESULT),
        mode="continue",
    )
    if tool_lifecycle_callback:
        tool_lifecycle_callback(tool_begin_event(tool_call, index))
        tool_lifecycle_callback(tool_end_event(tool_call, response, index))
    return response


def run_tools_sequentially(
    tools_called: list[dict[str, Any]],
    all_tools: list[dict[str, Any]],
    tool_lifecycle_callback: Callable[[dict[str, Any]], None] | None,
    base_index: int = 0,
) -> tuple[list[ToolResponse], dict[str, Any] | None]:
    """Execute tools one at a time, applying A1's run/suspend rule after a park.

    Every index in *tools_called* gets exactly one entry appended to the
    returned responses — either a real dispatch (``execute_tool``) or A1's
    synthesized suspension — so the positional invariant
    :func:`park_on_all_waits` relies on always holds, and the whole batch is
    always walked to completion (no early return at the first wait, unlike
    the pre-Phase-1 version of this function).

    Args:
        base_index: Running count of tool calls completed in EARLIER
            iterations of the same ``step()`` call — see
            :func:`brain_tool_batch.execute_tool_batch_concurrent` for why the
            lifecycle event's ``index`` must be turn-global rather than
            restarting at 0 for every batch.

    Returns:
        ``(tool_responses, park)`` where ``park`` is the dict returned by
        :func:`park_on_all_waits` (``None`` when nothing parked).
    """
    tool_responses: list[ToolResponse] = []
    suspended_indices: set = set()
    parked = False
    parked_count = 0

    for idx, tool_call in enumerate(tools_called):
        if parked and execution_policy(tool_call, all_tools, parked_count) == "suspend":
            tool_responses.append(
                _suspend_call(tool_call, tool_lifecycle_callback, base_index + idx)
            )
            suspended_indices.add(idx)
            continue

        if tool_lifecycle_callback:
            tool_lifecycle_callback(tool_begin_event(tool_call, base_index + idx))

        tool_response = execute_tool(tool_call, all_tools)

        if tool_lifecycle_callback:
            tool_lifecycle_callback(
                tool_end_event(tool_call, tool_response, base_index + idx)
            )
        tool_responses.append(tool_response)

        if tool_response.mode == "wait":
            parked = True
            parked_count += 1

    return tool_responses, park_on_all_waits(
        tool_responses, tools_called, frozenset(suspended_indices)
    )
