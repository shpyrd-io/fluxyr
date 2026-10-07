"""Real-model private Vault forms, answered through the browser in a disposable DB.

Run with --run, open :5059, and save the two printed synthetic test values in
the create/edit forms. Only natural-language goals are supplied to the model.
"""

import argparse
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from waitress import create_server

from fluxyr.app import create_app
from fluxyr.config import Settings
from fluxyr.database import row_dict
from fluxyr.models import (
    Decision,
    Effect,
    Event,
    Job,
    Message,
    Session,
    VaultItem,
)
from fluxyr.providers import make_adapter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if not args.run:
        parser.error("Pass --run to make real provider requests")
    repo = Path(__file__).resolve().parents[1]
    load_dotenv(repo / ".env")
    run = "vault_" + uuid.uuid4().hex[:12]
    root = repo / ".runtime/live-validation" / run
    values = [f"synthetic-private-{run}-create", f"synthetic-private-{run}-edit"]
    admin = create_engine(
        os.environ.get("TEST_DATABASE_URL") or Settings().database_url
    )
    with admin.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{run}"'))
    url = make_url(admin.url).update_query_dict({"options": f"-csearch_path={run}"})
    settings = Settings(
        root=root,
        database_url=url.render_as_string(hide_password=False),
        provider="openrouter",
        model="minimax/minimax-m3",
        workers=2,
        max_iterations=12,
        max_tokens=5000,
    )
    app = create_app(settings)
    engine = app.extensions["engine"]
    outgoing = []

    def real_adapter():
        adapter = make_adapter(engine.db.model_config(), engine.vault)
        execute = adapter.execute_step

        def audited_execute(*args, **kwargs):
            serialized = json.dumps([args, kwargs], default=str)
            assert not any(value in serialized for value in values), (
                "Secret in model input"
            )
            outgoing.append(serialized)
            return execute(*args, **kwargs)

        adapter.execute_step = audited_execute
        return adapter

    engine.adapter_factory = real_adapter
    server = create_server(app, host="127.0.0.1", port=5059, threads=8)
    threading.Thread(target=server.run, daemon=True).start()
    print(
        json.dumps(
            {
                "run": run,
                "url": "http://127.0.0.1:5059/",
                "synthetic_form_values": values,
            }
        ),
        flush=True,
    )
    engine.start()
    checks = {}
    try:
        for phase, prompt in enumerate(
            [
                "Quero cadastrar uma credencial de texto chamada weather_demo para usar futuramente numa habilidade de previsão do tempo. Confira se existe e abra o formulário privado para eu preencher. Não peça o valor no chat, não crie a habilidade agora e não execute chamadas de API. Depois que eu salvar, confirme que está pronta para ser referenciada pelo nome.",
                "Agora quero trocar o valor da credencial weather_demo que acabei de cadastrar, mantendo o mesmo item e nome. Abra novamente o formulário privado para edição; depois de salvar, confirme a atualização. Não leia nem imprima o valor.",
            ]
        ):
            job = engine.store.enqueue(prompt)
            print(json.dumps({"phase": phase, "job_id": job["id"]}), flush=True)
            deadline = time.monotonic() + 600
            previous = None
            while time.monotonic() < deadline:
                with engine.db.transaction() as db:
                    current = row_dict(db.get(Job, job["id"]))
                status = current["status"]
                if status != previous:
                    print(json.dumps({"phase": phase, "status": status}), flush=True)
                    previous = status
                if status in ("succeeded", "failed", "cancelled", "interrupted"):
                    break
                time.sleep(0.5)
            else:
                engine.store.control(job["id"], "cancel")
                raise AssertionError("Private form workflow timed out")
            assert status == "succeeded", current.get("error")
            checks[f"phase_{phase}_saved_private_value"] = (
                engine.vault.resolve("weather_demo")["value"] == values[phase]
            )
            with engine.db.transaction() as db:
                decisions = list(
                    db.scalars(select(Decision).where(Decision.job_id == job["id"]))
                )
                assert len(decisions) == 1
                reference = decisions[0].value["result"]
            checks[f"phase_{phase}_action"] = reference["action"] == (
                "create" if phase == 0 else "edit"
            )
            if phase == 0:
                original_id = reference["vault_item_id"]
            else:
                checks["edit_same_item"] = reference["vault_item_id"] == original_id
        with engine.db.transaction() as db:
            rows = {
                table.__tablename__: [
                    row_dict(row) for row in db.scalars(select(table))
                ]
                for table in (Job, Message, Session, Event, Decision, Effect, VaultItem)
            }
        checks["secrets_absent_from_context_and_events"] = not any(
            value in json.dumps([rows, outgoing], default=str) for value in values
        )
        checks["one_vault_item"] = len(rows[VaultItem.__tablename__]) == 1
        report = {
            "mock_model": False,
            "model": settings.model,
            "checks": checks,
            "provider_requests": len(outgoing),
            "records": rows,
        }
        (root / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str)
        )
        print(
            json.dumps({"checks": checks, "report": str(root / "report.json")}),
            flush=True,
        )
        assert all(checks.values())
    finally:
        engine.stop()
        server.close()
        engine.db.engine.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{run}" CASCADE'))
        admin.dispose()


if __name__ == "__main__":
    main()
