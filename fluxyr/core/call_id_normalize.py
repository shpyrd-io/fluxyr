"""A0 — guarantee every tool call in a turn gets a non-empty, unique call_id.

Split out of ``brain.py`` to keep that file under the project's file-size
guideline (round-3 amendment 5 names this extraction for Phase 1).

Why this exists: the N-park protocol (work item B) keys every decision on
``call_id``. ``execute_tool`` (``brain_tool_executor.py``) defaults a missing
id to ``""``, so two parked calls that both omit it would collapse onto one
``pending_tools`` entry, the all-decided predicate would never be satisfied,
and the session would be stuck ``WAITING`` forever. This is reachable beyond
a test double: the OpenAI-compatible adapter copies the call id verbatim from
the provider (``adapters/openai_execution.py``), and gateway models are known
to omit or blank it — the exact case this backstop is for.

Uniqueness must hold across the WHOLE session's lifetime, not just within one
``step()`` call: ``ai_chat_pua_persistence.settle_parked_pua_blocks`` matches
a settle's ``toolCallId`` against every assistant ``ChatMessage`` row for the
session, including ones already settled in an earlier turn (an already-settled
block still carries ``__pua__``, so it still matches). ``brain.py`` therefore
passes a ``step_seq`` that is a monotonically increasing counter scoped to the
whole brain (persisted via ``save()``/``restore()``/``from_saved_state()``,
incremented once per ``step()`` call) rather than the loop-local iteration
count — so two different turns of the same session can never mint the same
synthesized id, and a later turn's settle can never overwrite an earlier
turn's already-settled card.
"""

from typing import Any


def ensure_call_ids(
    response: dict[str, Any],
    tools_called: list[dict[str, Any]],
    *,
    step_seq: int,
    base_index: int,
) -> None:
    """Assign a synthetic ``call_id`` to every entry in *tools_called* that lacks one.

    Mutates *tools_called* entries IN PLACE — this is deliberate and load-bearing:
    ``response["function_calls"]`` (set by the Anthropic adapter) is the SAME list
    object as *tools_called*, so fixing the id here also fixes the id the next
    turn's ``_rebuild_content_blocks``/history-reconstruction path reads back.

    When *response* carries a native content-block LIST (``has_thinking=True``
    turns ``response['content']`` into a list of typed blocks instead of a plain
    string), the corresponding ``tool_use`` block is patched too — matched by
    POSITION: the k-th ``tool_use`` block in ``response['content']`` was appended
    in lockstep with the k-th entry of *tools_called* (see
    ``AnthropicAdapter._extract_content_blocks``), so writing only into
    *tools_called* would leave that block's ``id`` out of sync, and the next
    format pass would amputate it as an orphan (``adapters/anthropic.py``'s
    ``_clean_orphaned_tool_results`` Pass 2 drops a ``tool_use`` block whose id
    has no matching ``tool_result``).

    Args:
        response: The raw response dict ``execute_step_with_usage`` returned.
        tools_called: This iteration's tool calls, in original order.
        step_seq: A counter scoped to the WHOLE brain/session (not the loop-local
            iteration count) — part of the synthesized id so ids never collide
            across DIFFERENT turns of the same session (see the module
            docstring for why that matters, not just within one ``step()`` call).
        base_index: Turn-global count of tool calls completed in EARLIER
            iterations of this same ``step()`` call — part of the synthesized id
            so ids never collide within one iteration's batch either.
    """
    if not tools_called:
        return

    content = response.get("content") if isinstance(response, dict) else None
    tool_use_blocks = (
        [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
        if isinstance(content, list)
        else []
    )

    for i, tool_call in enumerate(tools_called):
        if tool_call.get("call_id"):
            continue
        synthetic_id = f"synth_{step_seq}_{base_index + i}"
        tool_call["call_id"] = synthetic_id
        if i < len(tool_use_blocks):
            tool_use_blocks[i]["id"] = synthetic_id
