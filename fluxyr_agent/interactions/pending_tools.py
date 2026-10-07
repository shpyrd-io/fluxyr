"""Shared constants and the single ``pending_status`` derivation for
``brain_state['pending_tools']`` entries.

Imported by the gate (``pua/decision_gate.py``), ``ChatSession.to_dict``,
``RoutineRun`` (via ``models/routine_run_view.py``), ``execution_service`` and
``executor_service`` — one definition so every reader agrees on what a legacy
row (persisted before N-approvals shipped, carrying no ``_status``) decides
to.

The parked-tools cap itself (``MAX_PARKED_TOOLS``) is owned by
``synthetic_brain.tool_execution_policy`` — the module that actually enforces
it in ``park_on_all_waits`` — and is not re-declared here to avoid two
sources of truth for the same limit.
"""

# `_decided_ids` is a cycle-spanning ring buffer: newest 32 decided call ids,
# kept so a re-post for an id from a superseded cycle still answers
# `duplicate: true` instead of a bare "not found" error.
MAX_DECIDED_IDS = 32

# A gate-fire cycle gets at most 3 attempts before the turn is abandoned.
MAX_GATE_FIRE_ATTEMPTS = 3

# A `RUNNING` row with no live stream and no heartbeat for this long is
# considered abandoned (process death mid-fire), never sooner — a Redis
# blip or a slow-but-alive brain step must not trigger a spurious recovery.
RECOVERY_RUNNING_TTL_SECONDS = 300

# A live gate-fire refreshes `_fire_owed.heartbeat_at` at most this often, so
# a long, quiet, LIVING brain.step doesn't look stale even when Redis-derived
# liveness is unavailable.
HEARTBEAT_MIN_INTERVAL_SECONDS = 60

# build_skill / rebuild_action emit their OWN closing frame (tool-call-progress
# with phase='done') from their re-execution branch — a generic
# `emit_terminal_close_frames` close would duplicate it. Declared here (a leaf
# both `pua/reexec_branches.py` and `ai_chat_resume_settle.py` already import)
# instead of in either of those two modules, which would otherwise need a
# mutual import of each other to share it.
NO_LIVE_CLOSE_TOOLS = ("build_skill", "rebuild_action")

# The user-supplied subset of a 'complete' decision's request body that is
# ever allowed through to either a settled card (ai_chat_resume_settle.py's
# build_settle_for_decided_entry) or a brain tool_result
# (pua/tool_outputs.py's build_tool_outputs) — never 'status': both callers
# stamp that field themselves via .setdefault, and it is deliberately absent
# here (not just from the narrower COMPLETE_INPUT_KEYS) so a client-supplied
# 'status' can never override the server-stamped default in either the
# persisted card or the brain tool_result. Declared here for the same
# reason as NO_LIVE_CLOSE_TOOLS: both callers need it, and declaring it in
# either one would force a mutual import of the other.
COMPLETE_ALLOWED_KEYS = (
    "action",
    "vault_item_id",
    "vault_item_name",
    "selected_index",
    "selected_label",
    "user_input",
)

_STATUS_VALUES = ("parked", "completed", "not_executed")


def pending_status(entry: dict) -> str:
    """Return one of 'parked' | 'completed' | 'not_executed' for a
    ``pending_tools`` entry.

    Written by ``park_on_all_waits`` and normalised onto every entry on the
    first decision write, but NEVER required on read: a row persisted before
    this field existed carries no ``_status`` at all, so the derivation below
    is what keeps such a row fully resolvable across the deploy with no data
    migration. `_status` is the only field this reads to decide 'parked'
    without a `__pua__` result — every other combination falls back to the
    result-shape check, matching every entry ever persisted.
    """
    status = entry.get("_status")
    if status in _STATUS_VALUES:
        return status
    result = entry.get("_result") or {}
    if isinstance(result, dict) and "__pua__" in result:
        return "parked"
    return "completed"


def is_decided(entry: dict) -> bool:
    """True when this entry carries a recorded ``_decision``."""
    return "_decision" in entry


def has_reexec(entry: dict) -> bool:
    """True when this entry's re-execution branch already committed
    (``_reexec`` present) — the durability check every re-execution branch
    makes before running so it is never invoked twice for the same call.
    """
    return "_reexec" in entry
