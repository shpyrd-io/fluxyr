"""Keep acquired conversation context without resuming cancelled work."""

import copy

from .core.tools.tool_response import ToolResponse
from .interactions.tool_outputs import build_tool_outputs


def cancelled_context(state):
    if not state or state.get("provider") in (None, "local"):
        return None
    if not state.get("short_term", {}).get("items"):
        return None
    saved = copy.deepcopy(state)
    pending = {t.get("call_id"): t for t in saved.get("pending_tools", [])}
    messages = saved["short_term"]["items"]
    history = []
    unanswered = {}

    def close_calls():
        for call_id, name in unanswered.items():
            entry = pending.get(call_id, {})
            if (
                entry.get("_status") in ("completed", "not_executed")
                or entry.get("_reexec") is not None
            ):
                # A parked batch can hold completed siblings outside short-term memory.
                result = build_tool_outputs([entry])[0].to_message()
            else:
                result = ToolResponse(
                    name,
                    call_id,
                    {
                        "status": "cancelled",
                        "error": "Execution cancelled by the user before a final result was recorded. Do not retry automatically; external effects may already have occurred.",
                    },
                ).to_message()
            history.append(result)
        unanswered.clear()

    for message in messages:
        if message.get("role") != "tool":
            close_calls()
        history.append(message)
        if message.get("role") == "assistant":
            for call in message.get("function_calls", []):
                unanswered[call["call_id"]] = call["name"]
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        unanswered[block["id"]] = block["name"]
        elif message.get("role") == "tool":
            unanswered.pop(message.get("tool_call_id"), None)
    close_calls()
    history.append(
        {
            "role": "assistant",
            "content": "[Execution cancelled by the user. Previous conversation and completed tool results are retained for reference. Pending work is stopped; follow the next user request.]",
        }
    )
    saved["short_term"]["items"] = history
    saved["pending_tools"] = []
    saved["current_state"] = "READY"
    saved["analysis_pending"] = False
    return saved
