"""Opt-in real-model build + typed human continuation in an isolated instance.

Open the printed URL to supply the test's human responses. No scripted model,
source code, or tool-call sequence is supplied. Persist the evidence locally.
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
from fluxyr.models import Event, Job, Message, Skill, Tool, ToolVersion
from fluxyr.database import row_dict


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument(
        "--provider",
        help="Optional OpenRouter provider slug for this isolated probe only",
    )
    parser.add_argument(
        "--resume-run", help="Reuse an isolated probe after an upstream failure"
    )
    args = parser.parse_args()
    if not args.run:
        parser.error("Pass --run to make real provider requests")
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    base = Settings()
    run = args.resume_run or "human-skill-" + uuid.uuid4().hex[:12]
    import re

    if not re.fullmatch(r"human-skill-[a-f0-9]{12}", run):
        parser.error("Invalid isolated run ID")
    schema = run.replace("-", "_")
    admin = create_engine(base.database_url)
    if not args.resume_run:
        with admin.begin() as c:
            c.execute(text(f'CREATE SCHEMA "{schema}"'))
    admin.dispose()
    url = make_url(base.database_url).update_query_dict(
        {"options": f"-csearch_path={schema}"}
    )
    settings = Settings(
        database_url=url.render_as_string(hide_password=False),
        root=root / ".runtime/live-validation" / run,
        workers=2,
    )
    app = create_app(settings)
    (settings.root / "stop").unlink(missing_ok=True)
    e = app.extensions["engine"]
    if args.provider:
        from fluxyr.providers import make_adapter

        def real_adapter():
            adapter = make_adapter(e.db.model_config(), e.vault)
            adapter._provider_preferences = {
                "order": [args.provider],
                "allow_fallbacks": False,
            }
            return adapter

        e.adapter_factory = real_adapter
    threading.Thread(
        target=lambda: serve(app, host="127.0.0.1", port=5059, threads=8), daemon=True
    ).start()
    job = e.store.enqueue(
        """Crie e teste uma habilidade chamada revisao_cartao. Quero uma única ação que prepare dois cartões HTML locais visualmente diferentes, Azul e Verde, mostre os previews para eu escolher um, depois pergunte em texto livre o título que eu quero, e finalmente peça confirmação para salvar. Após minha confirmação, salve em ./data um JSON com a opção original selecionada e o título exato, e retorne o caminho. Se eu recusar, não salve. Essa ação deve continuar depois de cada resposta, mantendo os cartões originais. Não simule minhas respostas. Teste a implementação e aguarde minhas interações; só ative após o teste real terminar com sucesso."""
    )
    print(
        json.dumps(
            {
                "run": run,
                "schema": schema,
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
                jobs = list(s.scalars(select(Job).order_by(Job.created_at)))
                state = [
                    (
                        j.id,
                        j.status,
                        [
                            (
                                p.get("name"),
                                (p.get("_result") or {})
                                .get("__pua__", {})
                                .get("payload", {})
                                .get("variant"),
                            )
                            for p in (j.brain or {}).get("pending_tools", [])
                            if not p.get("_decision")
                        ],
                    )
                    for j in jobs
                ]
                if state != last:
                    print(json.dumps({"jobs": state}), flush=True)
                    last = state
                root_job = s.get(Job, job["id"])
                if root_job.status in ("succeeded", "failed", "cancelled"):
                    report = {
                        "mock_model": False,
                        "provider_override": args.provider,
                        "run": run,
                        "status": root_job.status,
                        "error": root_job.error,
                        "jobs": [
                            {
                                k: v
                                for k, v in row_dict(j).items()
                                if k not in ("brain", "snapshot")
                            }
                            for j in jobs
                        ],
                        "events": [
                            row_dict(ev)
                            for ev in s.scalars(select(Event).order_by(Event.id))
                        ],
                        "messages": [
                            row_dict(m)
                            for m in s.scalars(
                                select(Message).order_by(Message.created_at)
                            )
                        ],
                        "skills": [row_dict(v) for v in s.scalars(select(Skill))],
                        "tools": [row_dict(v) for v in s.scalars(select(Tool))],
                        "versions": [
                            row_dict(v) for v in s.scalars(select(ToolVersion))
                        ],
                    }
                    (settings.root / "human-skill-report.json").write_text(
                        json.dumps(report, ensure_ascii=False, indent=2)
                    )
                    print(
                        json.dumps(
                            {
                                "status": root_job.status,
                                "report": str(
                                    settings.root / "human-skill-report.json"
                                ),
                            }
                        ),
                        flush=True,
                    )
                    break
            time.sleep(1)
        while not (settings.root / "stop").exists():
            time.sleep(1)
    finally:
        e.stop()


if __name__ == "__main__":
    main()
