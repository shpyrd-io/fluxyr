"""Real MiniMax build with activity evidence in an isolated PostgreSQL schema."""

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
from fluxyr_agent.app import create_app
from fluxyr_agent.config import Settings
from fluxyr_agent.models import Event, Job
from fluxyr_agent.usage import usage_report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if not args.run:
        parser.error("Pass --run for real provider requests")
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    base = Settings()
    run = uuid.uuid4().hex[:12]
    schema = "activity_probe_" + run
    admin = create_engine(base.database_url)
    with admin.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    admin.dispose()
    url = make_url(base.database_url).update_query_dict(
        {"options": f"-csearch_path={schema}"}
    )
    settings = Settings(
        database_url=url.render_as_string(hide_password=False),
        root=root / ".runtime/live-validation" / run,
        workers=1,
    )
    app = create_app(settings)
    engine = app.extensions["engine"]
    threading.Thread(
        target=lambda: serve(app, host="127.0.0.1", port=5059, threads=8), daemon=True
    ).start()
    job = engine.store.enqueue(
        "Crie uma habilidade saudacao_local com uma ação Python que recebe nome (string não vazia), retorna uma saudação em português e rejeita nome vazio. Não precisa de API nem credenciais. Construa, teste e ative a habilidade."
    )
    print(
        json.dumps(
            {
                "run": run,
                "job": job["id"],
                "url": "http://127.0.0.1:5059/",
                "model": engine.db.model_config()["model"],
            }
        ),
        flush=True,
    )
    engine.start()
    last = None
    try:
        for _ in range(1200):
            with engine.db.transaction() as s:
                state = s.get(Job, job["id"]).status
                if state != last:
                    print(json.dumps({"status": state}), flush=True)
                    last = state
                if state in ("succeeded", "failed", "cancelled", "waiting"):
                    rows = list(
                        s.scalars(
                            select(Event)
                            .where(Event.session_id == job["session_id"])
                            .order_by(Event.id)
                        )
                    )
                    activity = [r.payload for r in rows if r.type == "activity"]
                    report = {
                        "mock_model": False,
                        "usage": usage_report(engine.db),
                        "status": state,
                        "activity_count": len(activity),
                        "phases": sorted({p["phase"] for p in activity}),
                        "origins": sorted({p["origin_job_id"] for p in activity}),
                        "descendant_chunks": sum(
                            p["chunks"] for p in activity if p["depth"] > 0
                        ),
                        "parent_argument_chunks": sum(
                            p["chunks"]
                            for p in activity
                            if p["depth"] == 0 and p["phase"] == "generating_arguments"
                        ),
                        "events": [
                            {"id": r.id, "type": r.type, "payload": r.payload}
                            for r in rows
                            if r.type == "activity"
                        ],
                    }
                    (settings.root / "activity-report.json").write_text(
                        json.dumps(report, ensure_ascii=False, indent=2)
                    )
                    print(
                        json.dumps({k: v for k, v in report.items() if k != "events"}),
                        flush=True,
                    )
                    break
            time.sleep(0.5)
        while not (settings.root / "stop").exists():
            time.sleep(0.5)
    finally:
        engine.stop()


if __name__ == "__main__":
    main()
