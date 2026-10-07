"""Real-model parallel dispatch probe, isolated DB schema and local files.

Copies one existing dice action read-only, never alters production sessions.
Run explicitly with --run; writes evidence and optionally serves it for UI QA.
"""

import argparse
import json
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from waitress import serve

from fluxyr.app import create_app
from fluxyr.config import Settings
from fluxyr.database import Database, row_dict
from fluxyr.models import Event, Job, Message, Skill, Tool, ToolVersion
from fluxyr.providers import make_adapter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    if not args.run:
        parser.error("Pass --run to make real model requests")
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    base = Settings()
    production = Database(base)
    with production.transaction() as s:
        t = s.scalar(
            select(Tool).where(Tool.name == "action_rpg_dice_roller_roll_notation")
        )
        skill = row_dict(s.get(Skill, t.skill_id))
        version = row_dict(s.get(ToolVersion, t.active_version))
        tool = row_dict(t)
    production.engine.dispose()
    run = "parallel_" + uuid.uuid4().hex[:12]
    admin = create_engine(base.database_url)
    with admin.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{run}"'))
    admin.dispose()
    url = make_url(base.database_url).update_query_dict(
        {"options": f"-csearch_path={run}"}
    )
    settings = Settings(
        root=root / ".runtime/live-validation" / run,
        database_url=url.render_as_string(hide_password=False),
        workers=2,
    )
    app = create_app(settings)
    e = app.extensions["engine"]
    with e.db.transaction() as s:
        # Preserve original identifiers and exact source for the audit.
        s.add(Skill(**skill))
        s.flush()
        s.add(Tool(**{**tool, "active_version": None}))
        s.flush()
        s.add(ToolVersion(**{**version, "state": "candidate", "test_result": None}))
        s.flush()
    tested = e.skills.test(version["id"], {"notation": "2d20"})
    assert tested["passed"], tested
    e.skills.activate(version["id"])

    def adapter():
        model = make_adapter(e.db.model_config(), e.vault)
        model._provider_preferences = {"order": ["minimax"], "allow_fallbacks": False}
        return model

    e.adapter_factory = adapter
    job = e.store.enqueue("Execute em paralelo duas execuções de 2d20")
    print(
        json.dumps(
            {"run": run, "job_id": job["id"], "model": e.db.model_config()["model"]}
        ),
        flush=True,
    )
    if args.serve:
        threading.Thread(
            target=lambda: serve(app, host="127.0.0.1", port=5059, threads=8),
            daemon=True,
        ).start()
    e.start()
    deadline = time.monotonic() + 240
    try:
        while time.monotonic() < deadline:
            with e.db.transaction() as s:
                j = s.get(Job, job["id"])
                if j.status in ("succeeded", "failed", "cancelled", "waiting"):
                    events = [
                        row_dict(ev)
                        for ev in s.scalars(
                            select(Event).where(Event.job_id == j.id).order_by(Event.id)
                        )
                    ]
                    report = {
                        "mock_model": False,
                        "model": e.db.model_config()["model"],
                        "provider": "minimax",
                        "job_id": j.id,
                        "status": j.status,
                        "error": j.error,
                        "events": events,
                        "messages": [
                            row_dict(m)
                            for m in s.scalars(
                                select(Message).where(Message.job_id == j.id)
                            )
                        ],
                    }
                    (settings.root / "report.json").write_text(
                        json.dumps(report, ensure_ascii=False, indent=2)
                    )
                    print(
                        json.dumps(
                            {
                                "status": j.status,
                                "error": j.error,
                                "report": str(settings.root / "report.json"),
                                "batches": [
                                    ev["payload"]
                                    for ev in events
                                    if ev["type"] == "tool_batch_start"
                                ],
                            }
                        ),
                        flush=True,
                    )
                    break
            time.sleep(0.5)
        else:
            e.store.control(job["id"], "cancel")
            raise RuntimeError("Probe timed out")
        if args.serve:
            print(
                "UI on http://127.0.0.1:5059; touch stop in run folder to exit",
                flush=True,
            )
            while not (settings.root / "stop").exists():
                time.sleep(1)
    finally:
        e.stop()
        e.db.engine.dispose()


if __name__ == "__main__":
    main()
