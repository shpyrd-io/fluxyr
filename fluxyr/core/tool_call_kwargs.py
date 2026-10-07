"""Reserved kwarg names smuggled through a tool's callable signature.

Split out of brain_tool_executor.py to keep that file inside the project's
file-size budget (see .claude/guidelines/shared/code-quality.md — Limit
Source Files to 200-300 Lines). A leaf module so both brain_tool_executor.py
(the sender) and brain_tool_bridge.py (the receiver) can import the same
name without either depending on the other.
"""

from typing import Any

# Reserved kwarg name execute_tool (brain_tool_executor.py) uses to smuggle
# the call_id it is dispatching into brain_tool_bridge.build_brain_tools's
# tool_fn — see brain_tool_executor.execute_tool and
# tool_provider.ToolExecutionContext.tool_call_id. No ToolDefinition.parameters
# schema may declare a property with this name (the dunder wrapping matches
# this codebase's other reserved keys, __pua__ and __effects__).
TOOL_CALL_ID_KWARG = "__tool_call_id__"

# Reserved kwarg name a dispatcher uses to smuggle this call's effect-ledger
# coordinate (work item C, issue #870) into tool_fn alongside
# TOOL_CALL_ID_KWARG — a 2-tuple ``(coord, occurrence_hint)`` where ``coord``
# is ``(step_seq, turn_seq, call_seq)`` or None, and ``occurrence_hint`` is the
# brain's own per-tool dispatch counter or None. brain_tool_effect_wrap.
# build_call_ctx pops it (alongside TOOL_CALL_ID_KWARG) and folds it onto the
# per-call ToolExecutionContext as effect_coord/effect_occurrence.
#
# The producer is :func:`stamp_effect_coords` below, called from
# ``brain.py``'s ``step()`` right before either dispatcher runs — see that
# function's docstring for why the coordinate is computed there rather than
# inside ``brain_tool_dispatch.py``/``brain_tool_batch.py`` themselves.
TOOL_COORD_KWARG = "__tool_coord__"


def stamp_effect_coords(
    tools_called: list[dict[str, Any]],
    all_tools: list[dict[str, Any]],
    effect_occurrences: dict[str, int],
    *,
    step_seq: int,
    turn_seq: int,
    base_index: int,
) -> None:
    """Stamp ``coord``/``occurrence_hint`` onto every ``irreversible`` call in
    *tools_called*, mutating the dicts in place — the producer half of the
    effect ledger's chokepoint (work item C, #870).

    Called once per ``step()`` iteration, before either dispatcher
    (``run_tools_sequentially`` / ``execute_tool_batch_concurrent``) runs, so
    both paths forward a real coordinate: ``brain_tool_executor.execute_tool``
    reads ``tool_call.get("coord")``/``tool_call.get("occurrence_hint")``
    unconditionally, and a call with neither key set is exactly what makes
    the ledger's chokepoint a no-op (``claim_for_call``) — see that module.

    A non-``irreversible`` call is left untouched: the ledger already no-ops
    on it (``ctx.effect_coord is None``), and stamping it would only cost a
    wasted tuple.

    Args:
        tools_called: This iteration's tool calls, in original order —
            mutated in place.
        all_tools: The full tool list (with ``irreversible`` flags) this
            iteration is dispatching against.
        effect_occurrences: ``brain_state['_effect_occurrences']`` — the
            brain's own durable per-tool dispatch counter. Incremented here,
            BEFORE the ledger is ever consulted, for every ``irreversible``
            call regardless of the eventual outcome (the normative increment
            rule) — never reset by this function; the caller resets it
            whenever the effect scope changes.

            Normative constraint (3a-5): this hint counts EVERY stamped call,
            while the ledger's ``occurrence`` axis counts only calls that
            reached dispatch — the two axes drift apart by exactly one for
            each of THREE non-dispatching outcomes, and only two of the
            three are closed:
              - A1-suspended (never reaches the ledger at all): closed by
                ``brain.py``'s ``step()``, which decrements the hint for
                every such call before the next iteration stamps again.
              - a provably pre-dispatch ``failed`` row: closed by
                ``tool_effect_ledger._claim_replay``, which realigns a
                replay's hint onto the dispatched-only numbering via
                ``tool_effect_ledger_replay.failed_count`` (hint minus
                failed-row count == the next dispatched ordinal).
              - a ``refused``/``attempted`` outcome (the in-attempt guard,
                or a CLAIMED-coordinate re-read): NOT closed — no row is
                created or counted for either, so the hint is left one
                ahead with nothing to subtract. This is safe only because
                ``tool_effect_ledger.claim``'s in-attempt guard refuses
                every further call in the same scope while a sibling stays
                ``claimed`` (never falling through to a fresh claim), so the
                drift can never produce a duplicate dispatch — it is not
                because the invariant above holds in that case. A future
                change that relaxes the guard inherits this drift and must
                close it too, not just preserve the two-closer invariant.
        step_seq: The brain's durable per-``step()`` counter (``self.
            _turn_seq`` in ``brain.py`` — serialised across parks/resumes).
        turn_seq: This ``step()`` call's loop-local iteration count.
        base_index: Turn-global count of tool calls completed in EARLIER
            iterations of this same ``step()`` call — mirrors
            ``ensure_call_ids``'s own ``base_index`` so a coordinate's
            ``call_seq`` is unique across the whole turn, not just this batch.
    """
    irreversible_names = {t.get("name") for t in all_tools if t.get("irreversible")}
    for i, tool_call in enumerate(tools_called):
        name = tool_call.get("name")
        if name not in irreversible_names:
            continue
        hint = effect_occurrences.get(name, 0)
        effect_occurrences[name] = hint + 1
        tool_call["coord"] = (step_seq, turn_seq, base_index + i)
        tool_call["occurrence_hint"] = hint
