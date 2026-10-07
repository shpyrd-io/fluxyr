"""Real-model file workflow in an isolated DB schema and temporary data directory.

Does not prescribe a tool sequence or supply implementation code to the model.
Validates independent filesystem outcomes; saves actual tool events as evidence.
"""

import argparse
import csv
import json
import os
import random
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

from fluxyr.app import create_app
from fluxyr.config import Settings
from fluxyr.database import row_dict
from fluxyr.models import Event, Job, Message


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if not args.run:
        parser.error("Pass --run to authorize real provider calls")
    repo = Path(__file__).resolve().parents[1]
    load_dotenv(repo / ".env")
    run = "file_tools_" + uuid.uuid4().hex[:12]
    evidence = repo / ".runtime/live-validation" / run
    evidence.mkdir(parents=True)
    admin = create_engine(
        os.environ.get("TEST_DATABASE_URL") or os.environ["DATABASE_URL"]
    )
    with admin.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{run}"'))
    url = make_url(admin.url).update_query_dict({"options": f"-csearch_path={run}"})
    settings = Settings(
        root=evidence,
        database_url=url.render_as_string(hide_password=False),
        provider="openrouter",
        model="minimax/minimax-m3",
        workers=1,
        tool_timeout=30,
        execution_mode="local",
        max_iterations=18,
        max_tokens=6000,
    )
    app = create_app(settings)
    engine = app.extensions["engine"]
    folder = settings.data / ("project-" + uuid.uuid4().hex[:6])
    folder.mkdir()
    original = '# Preserve this comment: configuração original\n[report]\ntitle = "Draft"\nenabled = false\n'
    config = folder / "report.toml"
    config.write_text(original)
    rows = [
        (name, f"{random.randrange(1, 30000) / 100:.2f}")
        for name in ["Recife", "São Paulo", "Recife", "Natal", "São Paulo"]
    ]
    with (folder / "sales.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["city", "amount"])
        writer.writerows(rows)
    expected = {}
    for city, value in rows:
        expected[city] = expected.get(city, Decimal(0)) + Decimal(value)
    expected = {city: f"{value:.2f}" for city, value in expected.items()}
    prompt = """Existe um pequeno projeto em uma subpasta de data. Localize-o e inspecione os arquivos existentes. Faça somente estas alterações em report.toml: title vira "Relatório final" e enabled vira true, preservando todo o restante, incluindo o comentário. Crie nessa mesma pasta um script Python summarize.py que leia sales.csv, some os valores por cidade com precisão decimal e grave out/totals.json (objeto cidade => total como string com duas casas decimais). Execute o script, confira o arquivo gerado e me informe os totais. Gere out/report.html com esses resultados e abra o preview para mim. É uma tarefa avulsa de arquivos, não quero criar uma skill nem rotina. Não substitua os dados de entrada."""
    job = engine.store.enqueue(prompt)
    print(
        json.dumps(
            {
                "run": run,
                "model": settings.model,
                "mock_model": False,
                "job_id": job["id"],
            }
        ),
        flush=True,
    )
    engine.start()
    result = None
    try:
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            with engine.db.transaction() as db:
                result = row_dict(db.get(Job, job["id"]))
            if result["status"] in (
                "succeeded",
                "failed",
                "waiting",
                "interrupted",
                "cancelled",
            ):
                break
            time.sleep(0.5)
        else:
            engine.store.control(job["id"], "cancel")
            raise AssertionError("Live model exceeded 300 seconds")
        with engine.db.transaction() as db:
            events = [
                row_dict(ev)
                for ev in db.scalars(
                    select(Event).where(Event.job_id == job["id"]).order_by(Event.id)
                )
            ]
            messages = [
                row_dict(m)
                for m in db.scalars(select(Message).where(Message.job_id == job["id"]))
            ]
        calls = [ev["payload"] for ev in events if ev["type"] == "tool_end"]
        checks = {
            "finished": result["status"] == "succeeded",
            "precise_edit": config.read_text()
            == original.replace('"Draft"', '"Relatório final"').replace(
                "false", "true"
            ),
            "computed_totals": (folder / "out/totals.json").exists()
            and json.loads((folder / "out/totals.json").read_text()) == expected,
            "script_exists": (folder / "summarize.py").is_file(),
            "preview_created": (folder / "out/report.html").is_file()
            and any(ev["type"] == "preview" for ev in events),
            "four_tools_used": {"read", "write", "edit", "bash"}
            <= {call.get("tool_name") for call in calls},
        }
        report = {
            "mock_model": False,
            "job": result,
            "checks": checks,
            "expected": expected,
            "events": events,
            "messages": messages,
        }
        (evidence / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str)
        )
        print(
            json.dumps(
                {
                    "checks": checks,
                    "calls": [c.get("tool_name") for c in calls],
                    "report": str(evidence / "report.json"),
                }
            ),
            flush=True,
        )
        assert all(checks.values()), checks
    finally:
        engine.stop()
        engine.db.engine.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{run}" CASCADE'))
        admin.dispose()


if __name__ == "__main__":
    main()
