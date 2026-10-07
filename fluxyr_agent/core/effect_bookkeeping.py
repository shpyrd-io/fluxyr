"""Work item C's (#870) effect-ledger bookkeeping for ``SyntheticBrain``.

Split out of ``brain.py`` to keep that file inside the project's file-size
guideline (3a-4) — a straight lift, not a redesign: every function here reads
and writes the SAME attributes ``brain.py``'s inline code did, called from
the SAME four sites (``step()``'s dispatch-time stamp, ``step()``'s
post-park undo, ``save()``, ``restore()``/``from_saved_state()``). No
behaviour changes; only the import path does.
"""

from typing import Any

from fluxyr_agent.core.tool_call_kwargs import stamp_effect_coords


def stamp_step_effect_coords(
    tools_called: list[dict[str, Any]],
    all_tools: list[dict[str, Any]],
    effect_occurrences: dict[str, int],
    *,
    step_seq: int,
    turn_seq: int,
    base_index: int,
) -> None:
    """Stamp the ledger coordinate onto every ``irreversible`` call before
    either dispatcher runs — see ``tool_call_kwargs.stamp_effect_coords``'s
    docstring for the full contract. Called once per ``step()`` iteration."""
    stamp_effect_coords(
        tools_called,
        all_tools,
        effect_occurrences,
        step_seq=step_seq,
        turn_seq=turn_seq,
        base_index=base_index,
    )


def undo_not_executed_occurrence_hints(
    effect_occurrences: dict[str, int],
    pending_tools: list[dict[str, Any]],
) -> None:
    """Undo ``stamp_step_effect_coords``'s hint spend for calls A1 suspended.

    A1 suspension never reaches ``execute_tool``, so the stamp above already
    spent a hint for a call the ledger will never see a row for. Left
    uncorrected, the hint would permanently outrun the ledger's true per-tool
    count for the rest of this scope, and a later chat replay would misread
    an already-dispatched call as work beyond the window (see
    ``tool_effect_ledger.claim``'s replay comparison). Called only when a
    sequential-path dispatch parks.
    """
    for entry in pending_tools:
        if entry.get("_status") != "not_executed":
            continue
        if entry.get("occurrence_hint") is None:
            continue
        name = entry.get("name")
        if name in effect_occurrences:
            effect_occurrences[name] = max(0, effect_occurrences[name] - 1)


def save_effect_state(
    state: dict[str, Any],
    effect_occurrences: dict[str, int],
    effect_scope: dict[str, Any] | None,
) -> None:
    """Write work item C's two keys into a brain's ``save()`` output."""
    state["_effect_occurrences"] = effect_occurrences
    state["_effect_scope"] = effect_scope


def restore_effect_occurrences(state: dict[str, Any]) -> dict[str, int]:
    """Read back ``_effect_occurrences`` for ``restore()``. Old rows predate
    work item C and carry no key at all — ``{}`` is correct for them, not a
    defect.

    Documents the restore asymmetry (the caller's freshly-resolved scope
    wins): ``restore()`` intentionally reads ONLY this key, never
    ``_effect_scope`` — the caller (``create_chat_brain``) always passes the
    CURRENT scope explicitly to ``SyntheticBrain.__init__``, freshly resolved
    from the session row, and that must win over whatever this saved state
    carries. Reading ``_effect_scope`` back here would silently reinstate a
    stale scope on every turn after the first (§3.0). ``from_saved_state()``
    is the one reconstruction path that DOES read ``_effect_scope`` back (see
    :func:`load_effect_bookkeeping`), because it has no caller-supplied fresh
    scope to prefer in the first place.
    """
    return state.get("_effect_occurrences", {})


def load_effect_bookkeeping(
    state: dict[str, Any],
) -> tuple[dict[str, int], dict[str, Any] | None]:
    """Read back both ledger keys for ``from_saved_state()``.

    Unlike ``restore()`` (see :func:`restore_effect_occurrences`'s docstring
    for the asymmetry), this reconstruction path has no caller-supplied fresh
    scope to prefer, so ``_effect_scope`` is read back verbatim alongside
    ``_effect_occurrences``. Old rows predate work item C and carry neither.
    """
    return state.get("_effect_occurrences", {}), state.get("_effect_scope")
