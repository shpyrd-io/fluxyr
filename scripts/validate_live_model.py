"""Opt-in real-provider acceptance run, with an isolated PostgreSQL schema/root.

No scripted adapter and no supplied Python action source. Retains evidence in
.runtime/live-validation/<run id>. Credentials are read from the instance .env.
Run: .venv/bin/python scripts/validate_live_model.py --run [--serve]
"""

import argparse
import csv
import json
import logging
import sys
import threading
import time
import uuid
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

from fluxyr_agent.app import create_app
from fluxyr_agent.config import Settings
from fluxyr_agent.database import MAIN_SESSION, row_dict
from fluxyr_agent.models import Event, Job


def oracle(rows):
    totals = {}
    for city, amount in rows:
        city = city.strip().casefold()
        if city:
            totals[city] = totals.get(city, Decimal(0)) + Decimal(amount)
    return [
        {"city": city, "total": f"{total:.2f}"}
        for city, total in sorted(totals.items())
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run", action="store_true", help="Authorize paid provider requests"
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Keep verification UI at 127.0.0.1:5059 after validation",
    )
    parser.add_argument("--port", type=int, default=5059)
    args = parser.parse_args()
    if not args.run:
        parser.error("Pass --run to make real model calls")
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env", override=True)
    base = Settings()
    run_id = uuid.uuid4().hex[:12]
    schema = "live_validation_" + run_id
    with create_engine(base.database_url).begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = make_url(base.database_url).update_query_dict(
        {"options": f"-csearch_path={schema}"}
    )
    settings = Settings(
        database_url=url.render_as_string(hide_password=False),
        root=root / ".runtime" / "live-validation" / run_id,
        port=args.port,
        workers=1,
    )
    app = create_app(settings)
    e = app.extensions["engine"]
    report = {
        "run_id": run_id,
        "schema": schema,
        "model": e.db.model_config(),
        "mock_model": False,
        "checks": {},
    }
    print(
        json.dumps(
            {
                "run_id": run_id,
                "root": str(settings.root),
                "model": report["model"]["model"],
            }
        ),
        flush=True,
    )
    rows = [
        (" New York ", "12.30"),
        ("new york", "-2.10"),
        ("São Paulo", "0.10"),
        ("são paulo", "0.20"),
        ("", "99.99"),
    ]

    def write_csv(path, values):
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["city", "amount"])
            writer.writerows(values)

    write_csv(settings.data / "sales.csv", rows)
    prompt = """Crie uma skill reutilizável chamada CSV Totals com uma ação Python local. Ela recebe input_path e output_path (caminhos relativos a data), lê um CSV UTF-8 com cabeçalho city,amount, agrupa por cidade após strip e casefold, ignora cidade vazia e soma valores monetários decimais com precisão exata. Escreve no output_path um JSON cuja raiz é uma lista ordenada por cidade: cada item tem city e total, sendo total uma string com duas casas decimais. Retorne essa lista como resultado da ação também. Não use serviços externos, credenciais nem aprovação para essa transformação local autorizada. O arquivo sales.csv já existe no data root; leia-o com read_file(path="sales.csv") e não o sobrescreva nem crie outro arquivo de entrada. Implemente, teste com sales.csv e test-result.json, confira o arquivo e ative a ação. Crie uma rotina diária às 08:00 UTC chamada CSV Daily, mas mantenha o schedule DESABILITADO (enabled=false). A rotina deve usar a ação em sales.csv para gerar daily-result.json e verificar seu resultado. Enfileire uma execução manual dessa rotina agora. Não aguarde em polling dentro desta conversa: informe o ID enfileirado. Na resposta final use Markdown com um título, uma lista e uma tabela dos artefatos."""
    job = e.store.enqueue(prompt)
    report["build_job_id"] = job["id"]
    if args.serve:
        from waitress import serve

        threading.Thread(
            target=lambda: serve(app, host="127.0.0.1", port=args.port, threads=8),
            daemon=True,
        ).start()
    e.start()
    try:
        deadline = time.monotonic() + 600
        last_progress = 0
        while time.monotonic() < deadline:
            with e.db.transaction() as s:
                jobs = [
                    row_dict(j) for j in s.scalars(select(Job).order_by(Job.created_at))
                ]
                if time.monotonic() - last_progress > 15:
                    print(
                        json.dumps(
                            {
                                "jobs": [
                                    {"id": j["id"], "status": j["status"]} for j in jobs
                                ]
                            }
                        ),
                        flush=True,
                    )
                    last_progress = time.monotonic()
            if any(
                j["status"] in ("failed", "waiting", "paused", "interrupted")
                for j in jobs
            ):
                raise AssertionError(
                    "Execution failed or needs human intervention; inspect retained trace"
                )
            if len(jobs) >= 2 and all(j["status"] == "succeeded" for j in jobs):
                break
            if jobs and all(j["status"] == "succeeded" for j in jobs):
                raise AssertionError(
                    "Model ended without enqueuing the requested routine"
                )
            time.sleep(1)
        else:
            raise AssertionError("Real-provider acceptance run timed out")
        skills = e.skills.list()
        active = [t for skill in skills for t in skill["tools"] if t["active_version"]]
        assert len(active) == 1, "Expected one reusable active action"
        tool = active[0]
        report["function_name"] = tool["name"]
        report["checks"]["model_built_tested_activated"] = True
        with (settings.data / "sales.csv").open(newline="", encoding="utf-8") as f:
            assert list(csv.reader(f))[1:] == [list(row) for row in rows], (
                "Existing input file was changed"
            )
        report["checks"]["existing_input_preserved"] = True
        expected = oracle(rows)
        assert json.loads((settings.data / "daily-result.json").read_text()) == expected
        report["checks"]["manual_routine_file_matches_oracle"] = True
        routines = e.routines.list()
        assert (
            len(routines) == 1
            and routines[0]["cron"] == "0 8 * * *"
            and routines[0]["timezone"] == "UTC"
            and not routines[0]["enabled"]
        )
        report["checks"]["utc_schedule_saved_disabled"] = True
        # These inputs are generated only AFTER the model has finished writing
        # and activating its code. Never give the model an oracle implementation.
        holdout = [
            (" München ", "103.01"),
            ("MÜNCHEN", "-0.02"),
            ("東京", "0.01"),
            ("東京", "0.09"),
            ("Zürich", "999999.99"),
            (" ", "500.00"),
        ]
        write_csv(settings.data / "unseen.csv", holdout)
        result = e.skills.test(
            tool["active_version"],
            {"input_path": "unseen.csv", "output_path": "unseen-result.json"},
        )
        assert result["passed"] and result["output"] == oracle(holdout), result
        assert json.loads((settings.data / "unseen-result.json").read_text()) == oracle(
            holdout
        )
        report["checks"]["unseen_inputs_match_independent_oracle"] = True
        report["success"] = True
    except Exception as exc:
        report["success"] = False
        report["error"] = str(exc)
    finally:
        e.stop()
        with e.db.transaction() as s:
            events = [row_dict(v) for v in s.scalars(select(Event).order_by(Event.id))]
            report["jobs"] = [
                {k: j[k] for k in ("id", "status", "outcome", "error")}
                for j in (row_dict(v) for v in s.scalars(select(Job)))
            ]
        report["tool_calls"] = [
            v["payload"]["tool_name"] for v in events if v["type"] == "tool_begin"
        ]
        report["reasoning_chunks"] = sum(v["type"] == "reasoning" for v in events)
        report["text_chunks"] = sum(v["type"] == "delta" for v in events)
        (settings.root / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2)
        )
        (settings.root / "events.json").write_text(
            json.dumps(events, ensure_ascii=False, indent=2)
        )
        (settings.root / "skills.json").write_text(
            json.dumps(e.skills.list(), ensure_ascii=False, indent=2)
        )
        print(json.dumps(report, ensure_ascii=False), flush=True)
    if args.serve:
        print(
            "Verification UI retained at http://127.0.0.1:5059; Ctrl-C stops it.",
            flush=True,
        )
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    return 0 if report["success"] else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    raise SystemExit(main())
