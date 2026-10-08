"""Private browser input uses the approval lifecycle, never its free-text answer."""

import hashlib

from sqlalchemy import select

from ..models import Decision, Job
from .envelope import build_approval_pua


def request_input(engine, job, args, stop):
    args = dict(args)
    title = args.pop("title", "Private browser input")
    item_id = args.pop("vault_item_id", None)
    field = args.pop("field", None)
    submit = args.pop("submit", False)
    item_name = None
    if item_id:
        item = next(
            (item for item in engine.vault.list() if item["id"] == item_id), None
        )
        if not item:
            raise ValueError("Choose an existing Vault item")
        item_name = item["name"]
    target = engine.browsers.prepare(job["session_id"], stop=stop, **args)
    return {
        "__pua__": build_approval_pua(
            "confirm-reject" if item_id else "continue",
            title,
            "The value is sent directly to the browser; the agent receives only the outcome.",
            kind="browser_private_input",
            target=target,
            vault_item_id=item_id,
            vault_item_name=item_name,
            field=field,
            submit=submit,
        )
    }, "wait"


def submit_input(engine, job_id, request_id, body):
    if not isinstance(body, dict) or set(body) - {"value"}:
        raise ValueError("Invalid private input form")
    # Read/validate separately: never hold a DB transaction while driving Chrome.
    with engine.db.transaction() as db:
        job = db.get(Job, job_id)
        if not job:
            raise ValueError("Job not found")
        old = db.scalar(
            select(Decision).where(
                Decision.job_id == job_id, Decision.call_id == request_id
            )
        )
        if old:
            return {"duplicate": True, "status": job.status}
        entry = next(
            (
                p
                for p in (job.brain or {}).get("pending_tools", [])
                if (p.get("_request_id") or p.get("call_id")) == request_id
            ),
            None,
        )
        if (
            job.status != "waiting"
            or not entry
            or entry.get("name") != "browser_request_input"
            or entry.get("_status") != "parked"
            or entry.get("_decision")
        ):
            raise ValueError("Private input request is no longer current")
        payload = entry["_result"]["__pua__"]["payload"]
        if payload.get("vault_item_id"):
            if body:
                raise ValueError(
                    "This request authorizes a Vault item; it takes no value"
                )
        elif (
            not isinstance(body.get("value"), str)
            or not 1 <= len(body["value"]) <= 10000
        ):
            raise ValueError("Enter a private value of 1–10000 characters")

    def stopped():
        if engine.stopping.is_set():
            return True
        with engine.db.transaction() as db:
            return db.scalar(select(Job.status).where(Job.id == job_id)) != "waiting"

    def deliver():
        try:
            return engine.browsers.fill(
                payload["target"],
                value=body.get("value"),
                vault_item_id=payload.get("vault_item_id"),
                field=payload.get("field"),
                submit=payload.get("submit", False),
                stop=stopped,
            )
        except ValueError:
            return {
                "filled": False,
                "error": "Private delivery failed or browser expired. Inspect the page before requesting input again.",
            }

    # A second POST cannot fill or submit twice, even if the first HTTP response was lost.
    delivery_name = (
        "private_browser_delivery_" + hashlib.sha256(request_id.encode()).hexdigest()
    )
    outcome = engine.effects.run(
        job_id,
        delivery_name,
        0,
        {"request_id": request_id},
        deliver,
    )
    if "filled" not in outcome:
        # A concurrent delivery may still be in progress, or a prior host died.
        # Do not let a second POST settle the card before the first delivery.
        raise ValueError(
            "Delivery is in progress or its outcome is uncertain. Inspect the browser; do not resend the value automatically."
        )
    with engine.db.transaction() as db:
        job = db.get(Job, job_id, with_for_update=True)
        old = db.scalar(
            select(Decision).where(
                Decision.job_id == job_id, Decision.call_id == request_id
            )
        )
        if old:
            return {"duplicate": True, "status": job.status}
        entry = next(
            (
                p
                for p in (job.brain or {}).get("pending_tools", [])
                if (p.get("_request_id") or p.get("call_id")) == request_id
            ),
            None,
        )
        if job.status != "waiting" or not entry or entry.get("_decision"):
            raise ValueError("Private input request is no longer current")
        return engine.store._record_decision(
            db, job, entry, request_id, {"decision": "complete", "result": outcome}
        )
