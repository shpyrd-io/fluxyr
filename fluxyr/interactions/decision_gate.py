"""The pure, row-agnostic decision gate core for N simultaneous approvals.

``apply_decision`` takes a plain ``brain_state`` dict and returns a
``GateOutcome`` describing what the caller should persist. It never opens a
database session, never reads a model row, and never imports a chat- or
routine-specific module — the two thin wrappers
(``ai_chat_decision_store.py`` for chat, ``ExecutorService.resume_run`` for
routines) own the row lock, the authorization check and the liveness policy,
then hand this function a dict and get one back. This is what lets one test
suite exercise every branch with no database at all (R4A-6).

Terminology: a "cycle" is one park-to-resolution lifecycle of `pending_tools`.
A "gate-fire" is the request that closes the last undecided entry (or a
recovery re-post) and therefore drives exactly one `brain.step()`.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from fluxyr.interactions.pending_tools import (
    MAX_GATE_FIRE_ATTEMPTS,
    is_decided,
    pending_status,
)


@dataclass
class GateOutcome:
    """What the gate decided. The caller (a thin wrapper) is responsible for
    actually writing ``new_brain_state`` to its row and, on ``abandon=True``,
    calling the shared ``abandon_turn`` writer — the core never does either.
    """

    duplicate: bool = False
    resuming: bool = False
    pending_remaining: int = 0
    recovery_exhausted: bool = False
    abandon: bool = False
    new_brain_state: dict | None = None
    snapshot: dict | None = None
    fire_owed: dict | None = None
    error: str | None = None


def _undecided_parked(pending: list[dict]) -> list[dict]:
    return [t for t in pending if pending_status(t) == "parked" and not is_decided(t)]


def _fresh_fire_owed(now: str, attempt: int) -> dict:
    # No `high_water` key: the chat replay window is read FRESH from the
    # ledger at step 5, immediately before each fire's own dispatch stamp
    # (accepted amendment) — a persisted copy would be one fire stale and
    # has no consumer, so it is never written here.
    return {
        "cycle_id": str(uuid.uuid4()),
        "attempt": attempt,
        "started_at": now,
        "heartbeat_at": now,
        "dispatched": False,
    }


def apply_decision(
    brain_state: dict,
    tool_call_id: str,
    decision: str | None,
    reason: str | None,
    result: dict | None,
    *,
    now: "str | datetime",
    abandoned: bool,
    fire_only: bool = False,
) -> GateOutcome:
    """Evaluate one decision (or a fire-only recovery probe) against
    ``brain_state`` and return the outcome. Pure — no I/O, no row.

    Args:
        brain_state: The session/run's current ``brain_state`` dict (or
            ``{}``/``None``-coalesced by the caller).
        tool_call_id: The parked call id the request names. For
            ``fire_only=True`` this may be ANY decided id of the current
            cycle — it is used only to look up the cycle, never re-decided.
        decision: One of 'approve' | 'reject' | 'complete'. Ignored (must be
            None) when ``fire_only=True``.
        now: An ISO-8601 string (or ``datetime`` — the caller picks one
            representation and is consistent about it; this function never
            parses it, only stores/compares it as an opaque token except
            where the wrapper already resolved liveness into ``abandoned``).
        abandoned: Precomputed by the caller (fail-closed liveness rule for
            chat; always False for the routine wrapper — see
            ``ai_chat_decision_store.compute_abandoned``).
        fire_only: True for the recovery probe (``?fire_only=1``) — never
            records a decision, only fires an owed gate or reports it cannot.
    """
    now_str = now.isoformat() if isinstance(now, datetime) else now
    bs = dict(brain_state or {})
    pending: list[dict] = list(bs.get("pending_tools") or [])
    current_state = bs.get("current_state")
    decided_ids = list(bs.get("_decided_ids") or [])
    gate_fire_attempts = int(bs.get("_gate_fire_attempts") or 0)

    idx = next(
        (i for i, t in enumerate(pending) if t.get("call_id") == tool_call_id), None
    )
    entry = pending[idx] if idx is not None else None

    # Branch 0 — abandoned-RUNNING normalisation (R3A-7 / R4A-1 / R4A-5).
    # A fire is owed ONLY if the gate itself said so via `_fire_owed` — never
    # inferred from "every parked entry is decided", which a COMPLETED cycle
    # satisfies forever.
    if (
        current_state == "RUNNING"
        and bs.get("_fire_owed")
        and abandoned
        and pending
        and all(is_decided(t) for t in pending if pending_status(t) == "parked")
    ):
        bs = {**bs, "current_state": "WAITING"}
        current_state = "WAITING"

    # Branch 1 — unknown id.
    if entry is None:
        if tool_call_id in decided_ids:
            # A repeat fire_only after the turn was already abandoned lands
            # here (abandon_turn clears pending_tools but keeps the id in
            # _decided_ids) — report recovery_exhausted so the client renders
            # the abandon notice instead of silently doing nothing (§4.1).
            return GateOutcome(
                duplicate=True,
                resuming=False,
                pending_remaining=len(_undecided_parked(pending)),
                recovery_exhausted=bool(bs.get("_abandoned_at")),
            )
        if current_state != "WAITING":
            return GateOutcome(error="Session is not waiting for approval")
        return GateOutcome(error="Tool call not found in pending state")

    # Branch 2 — known but never parked (or already resolved out of parked).
    if pending_status(entry) != "parked":
        return GateOutcome(error="Tool call not found in pending state")

    # Branch 3 — already decided. NEVER an error, whatever current_state says.
    if is_decided(entry):
        undecided = _undecided_parked(pending)
        if not undecided and current_state == "WAITING":
            if gate_fire_attempts >= MAX_GATE_FIRE_ATTEMPTS:
                # Budget spent. The core only REPORTS this — the chat wrapper
                # calls the shared `abandon_turn` writer inside the lock it
                # already holds (accepted amendment: the core stays row-free,
                # so R4A-6's "no row at all" pin keeps holding).
                if bs.get("_abandoned_at"):
                    return GateOutcome(
                        duplicate=True, resuming=False, recovery_exhausted=True
                    )
                return GateOutcome(
                    duplicate=True,
                    resuming=False,
                    recovery_exhausted=True,
                    abandon=True,
                )
            # Deadlock / abandon recovery — a real branch, not a comment.
            new_pending = [{**t, "_status": pending_status(t)} for t in pending]
            attempt = gate_fire_attempts + 1
            owed = dict(bs.get("_fire_owed") or {})
            owed.update(
                {"attempt": attempt, "started_at": now_str, "heartbeat_at": now_str}
            )
            owed.setdefault("cycle_id", str(uuid.uuid4()))
            owed.setdefault("dispatched", False)
            new_bs = {
                **bs,
                "pending_tools": new_pending,
                "current_state": "RUNNING",
                "_gate_fire_attempts": attempt,
                "_fire_owed": owed,
            }
            return GateOutcome(
                duplicate=True,
                resuming=True,
                pending_remaining=0,
                new_brain_state=new_bs,
                snapshot={**bs, "pending_tools": new_pending},
                fire_owed=owed,
            )
        # Still has undecided siblings, or current_state is not WAITING
        # (e.g. a repost while a live gate-fire is RUNNING): nothing mutated.
        return GateOutcome(
            duplicate=True, resuming=False, pending_remaining=len(undecided)
        )

    # Branch 3b — fire_only reached an undecided entry: nothing to recover.
    if fire_only:
        return GateOutcome(resuming=False)

    # Branch 4 — undecided entry, session genuinely not waiting.
    if current_state != "WAITING":
        return GateOutcome(error="Session is not waiting for approval")

    # Branch 5 — record the decision. Rebuild as NEW dicts in a NEW list.
    new_pending = []
    for i, t in enumerate(pending):
        t2 = {**t, "_status": pending_status(t)}
        if i == idx:
            t2["_decision"] = {
                "decision": decision,
                "reason": reason,
                "result": result,
                "decided_at": now_str,
            }
        new_pending.append(t2)
    undecided = _undecided_parked(new_pending)
    resuming = not undecided
    bs2 = {**bs, "pending_tools": new_pending}
    fire_owed = None
    if resuming:
        attempt = gate_fire_attempts + 1
        fire_owed = _fresh_fire_owed(now_str, attempt)
        bs2["current_state"] = "RUNNING"
        bs2["_gate_fire_attempts"] = attempt
        bs2["_fire_owed"] = fire_owed
    return GateOutcome(
        duplicate=False,
        resuming=resuming,
        pending_remaining=len(undecided),
        new_brain_state=bs2,
        snapshot={**bs, "pending_tools": new_pending},
        fire_owed=fire_owed,
    )
