"""Row-agnostic §3.2.3 step 4 tool-output builders shared by BOTH surfaces
(chat and routine).

Extracted out of ``ai_chat_resume_settle.py`` (2a-2, D2): that module's own
name made it read as chat-specific, yet ``executor_service.py`` (the
routine surface) imported ``build_tool_outputs`` from it directly — exactly
the package-boundary crossing ``pua/decision_gate.py``'s own module
docstring forbids ("never imports a chat- or routine-specific module").
``build_tool_outputs``/``result_unavailable`` never touch a row, a session
or a run — they map a ``pending_tools`` list to plain ``ToolResponse``
objects — so both surfaces now import them from here instead, and a future
edit made "for chat" cannot silently change routine behaviour.

``settles_for`` deliberately did NOT move here too (a second pass fixed the
first one, which moved it and left it importing ``ai_chat_resume_settle``
right back — the exact crossing this split exists to remove, just one hop
later). It calls back into that module's own
``build_settle_for_decided_entry`` and has one caller, the chat surface, so
it stays in ``ai_chat_resume_settle.py`` instead. This module imports
NOTHING from ``app.services.ai_chat_*``.
"""

from fluxyr.core.tools.tool_response import ToolResponse
from fluxyr.interactions.pending_tools import (
    COMPLETE_ALLOWED_KEYS,
    pending_status,
)


def result_unavailable() -> dict:
    """The §3.2.3 step 4 fallback for an entry with no usable ``_result`` at
    all (a pre-deploy sibling). Never ``None`` — ``json.dumps`` must not put
    a ``null`` tool result on the wire.
    """
    return {
        "error": "result_unavailable",
        "executed": False,
        "reason": "This tool's result was not retained across the approval pause.",
    }


def build_tool_outputs(pending_tools: list[dict]) -> list:
    """§3.2.3 step 4's total mapping: exactly ``len(pending_tools)`` outputs,
    all ``mode='continue'``, in ``pending_tools`` order — completed and
    suspended siblings, re-executed parked entries (replayed, never re-run),
    and the pre-executed-approval / complete / reject shapes for the rest.
    """
    outputs = []
    for t in pending_tools:
        name = t.get("name", "")
        call_id = t.get("call_id")
        status = pending_status(t)
        if status in ("completed", "not_executed"):
            outputs.append(
                ToolResponse(
                    tool_name=name,
                    call_id=call_id,
                    result=t.get("_result") or result_unavailable(),
                    mode="continue",
                )
            )
            continue
        reexec = t.get("_reexec")
        if reexec is not None:
            result = reexec.get("result")
            outputs.append(
                ToolResponse(
                    tool_name=name,
                    call_id=call_id,
                    result=result if result is not None else result_unavailable(),
                    mode="continue",
                )
            )
            continue
        decision_info = t.get("_decision") or {}
        decision = decision_info.get("decision")
        prior_result = t.get("_result")
        if decision == "approve":
            if isinstance(prior_result, dict):
                result = {k: v for k, v in prior_result.items() if k != "__pua__"}
            else:
                result = {"status": "approved", "approver": "user", "tool_name": name}
        elif decision == "complete":
            result = {
                k: v
                for k, v in (decision_info.get("result") or {}).items()
                if k in COMPLETE_ALLOWED_KEYS and isinstance(v, (str, int))
            }
            result.setdefault("status", "completed")
        elif decision == "reject":
            result = {
                "error": f"Rejected: {decision_info.get('reason') or 'No reason provided'}",
                "rejected": True,
            }
        else:
            result = result_unavailable()
        outputs.append(
            ToolResponse(
                tool_name=name, call_id=call_id, result=result, mode="continue"
            )
        )
    return outputs
