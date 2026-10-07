"""Claim before execution, replay completed effects, refuse uncertain retries."""

import hashlib
import json

from sqlalchemy import select

from ..models import Effect


class Effects:
    def __init__(self, db):
        self.db = db

    def run(self, job_id, name, occurrence, args, fn):
        fingerprint = hashlib.sha256(
            json.dumps(args, sort_keys=True).encode()
        ).hexdigest()
        with self.db.transaction() as s:
            effect = s.scalar(
                select(Effect)
                .where(
                    Effect.job_id == job_id,
                    Effect.tool_name == name,
                    Effect.occurrence == occurrence,
                )
                .with_for_update()
            )
            if effect:
                if effect.fingerprint != fingerprint:
                    return {
                        "error": "Effect replay arguments changed; manual review required"
                    }
                if effect.status == "done":
                    return effect.result
                if effect.status != "parked":
                    return {
                        "error": "Previous effect outcome is uncertain; automatic retry refused"
                    }
                effect.status = "claimed"
            else:
                effect = Effect(
                    job_id=job_id,
                    tool_name=name,
                    occurrence=occurrence,
                    fingerprint=fingerprint,
                )
                s.add(effect)
                s.flush()
            effect_id = effect.id
        try:
            result = fn()
        except Exception as exc:
            # Keep the claim uncertain. The operation may already have reached an external API.
            raise RuntimeError(
                f"Execution failed; review possible side effects: {exc}"
            ) from exc
        with self.db.transaction() as s:
            effect = s.get(Effect, effect_id, with_for_update=True)
            effect.result = result
            effect.status = "parked" if "waiting" in result else "done"
        return result
