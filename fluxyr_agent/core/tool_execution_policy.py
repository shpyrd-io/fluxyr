"""A1's run/suspend rule for a tool call positioned after a park (work item A).

Split out of ``brain_tool_dispatch.py`` to keep that file under the
project's file-size guideline — this phase's own rewrite of
``park_on_all_waits``/A2b is what pushes it toward the limit. This is the
module the Phase 1 file table (§5 of the brief) names for ``execution_policy``.
"""

from typing import Any

# A1: at most this many tools may park in one turn (Issue #1004 Option C's N
# cap). Once this many calls have returned mode="wait", every later call in
# the batch is suspended regardless of its own side_effecting/requires_approval
# flags — see execution_policy.
MAX_PARKED_TOOLS = 8


def execution_policy(
    tool_call: dict[str, Any],
    tools: list[dict[str, Any]],
    parked_count: int,
) -> str:
    """A1's run/suspend rule for a call positioned after the first park.

    Governs the SEQUENTIAL dispatch path (``run_tools_sequentially``) only —
    the concurrent path is protected instead by the ``parallel_safe``
    declaration (no ``side_effecting`` tool ever declares it).

    | side_effecting | requires_approval | Action |
    |---|---|---|
    | False (explicit) | any | run — the provider positively declared this
    |                   |     | call cannot fire an unapproved side effect |
    | True | True | run — the call is itself gated; the card and the
    |      |       | decision still govern what the model is told |
    | True | False | suspend |
    | None (unclassified) | any | suspend — fail closed; see below |
    | any | any | suspend once ``MAX_PARKED_TOOLS`` waits have already
    |     |     | happened in this batch (the N cap) |

    Mirrors ``is_parallel_eligible``'s conservative default: a tool absent
    from *tools* suspends rather than runs. The same conservatism applies to
    ``side_effecting`` itself: only an EXPLICIT ``False`` runs a call after a
    park. ``side_effecting`` defaults to ``None`` on ``ToolDefinition`` (an
    opt-in, newly introduced flag), so a provider nobody has classified yet —
    today that is every provider outside the four §3.5 names — suspends
    rather than silently inheriting "safe to run" from an unset field. Before
    this phase NOTHING ever ran after a park; treating "unclassified" as "run"
    would have been a regression for every tool this phase never audited.

    Up to ``MAX_PARKED_TOOLS`` approval-gated (pre-executed) irreversible
    effects may therefore fire before any decision is made — running a call
    with ``requires_approval=True`` is what lets it produce its OWN ``__pua__``
    card in the first place (#1004). A reject on any one card governs only
    what the model is told; it does not un-fire an effect that already ran on
    a sibling card the user has not yet reached (round-3 amendment 2).
    """
    if parked_count >= MAX_PARKED_TOOLS:
        return "suspend"
    tool_name = tool_call.get("name", "")
    for tool in tools:
        if tool.get("name") == tool_name:
            if tool.get("side_effecting") is False:
                return "run"
            if tool.get("side_effecting") is True:
                return "run" if tool.get("requires_approval") else "suspend"
            return "suspend"  # None / missing — unclassified, fail closed
    return "suspend"
