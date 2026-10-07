"""Opt-in real-provider human interaction probe in an isolated PostgreSQL schema."""

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
from fluxyr_agent.models import Event, Job, Message


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if not args.run:
        parser.error("Pass --run to make real provider requests")
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    base = Settings()
    run = uuid.uuid4().hex[:12]
    schema = "human_probe_" + run
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
    e = app.extensions["engine"]
    threading.Thread(
        target=lambda: serve(app, host="127.0.0.1", port=5059, threads=8), daemon=True
    ).start()
    job = e.store.enqueue(
        "Antes de continuar, pergunte pela ferramenta ask_human qual unidade de temperatura eu prefiro: Celsius ou Fahrenheit. Aguarde minha resposta. Depois confirme minha escolha em uma frase, sem criar nem executar nenhuma outra ferramenta."
    )
    print(
        json.dumps(
            {
                "run": run,
                "job_id": job["id"],
                "url": "http://127.0.0.1:5059/",
                "model": e.db.model_config()["model"],
            }
        ),
        flush=True,
    )
    e.start()
    last = None
    try:
        for _ in range(1800):
            with e.db.transaction() as s:
                row = s.get(Job, job["id"])
                status = row.status
                if status != last:
                    print(
                        json.dumps({"status": status, "error": row.error}), flush=True
                    )
                    last = status
                if status in ("succeeded", "failed", "cancelled"):
                    report = {
                        "mock_model": False,
                        "status": status,
                        "events": [
                            {"type": ev.type, "payload": ev.payload}
                            for ev in s.scalars(
                                select(Event)
                                .where(Event.job_id == job["id"])
                                .order_by(Event.id)
                            )
                        ],
                        "messages": [
                            m.content
                            for m in s.scalars(
                                select(Message).where(Message.job_id == job["id"])
                            )
                        ],
                    }
                    (settings.root / "human-report.json").write_text(
                        json.dumps(report, ensure_ascii=False, indent=2)
                    )
                    print(
                        json.dumps(
                            {"report": str(settings.root / "human-report.json")}
                        ),
                        flush=True,
                    )
                    break
            time.sleep(0.5)
        # Keep the isolated UI available for final visual inspection.
        while not (settings.root / "stop").exists():
            time.sleep(0.5)
    finally:
        e.stop()


if __name__ == "__main__":
    main()
