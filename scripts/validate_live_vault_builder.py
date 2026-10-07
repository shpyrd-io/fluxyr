"""Opt-in MiniMax workbench/builder integration with synthetic typed credentials."""

import argparse
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from dotenv import load_dotenv
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

from fluxyr.app import create_app
from fluxyr.config import Settings
from fluxyr.database import row_dict
from fluxyr.models import Event, Job, Message, Tool, ToolVersion
from fluxyr.providers import make_adapter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    if not parser.parse_args().run:
        parser.error("Pass --run to make real provider requests")
    repo = Path(__file__).resolve().parents[1]
    load_dotenv(repo / ".env")
    run = "vault_build_" + uuid.uuid4().hex[:12]
    admin = create_engine(
        os.environ.get("TEST_DATABASE_URL") or Settings().database_url
    )
    with admin.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{run}"'))
    url = make_url(admin.url).update_query_dict({"options": f"-csearch_path={run}"})
    settings = Settings(
        root=repo / ".runtime/live-validation" / run,
        database_url=url.render_as_string(hide_password=False),
        provider="openrouter",
        model="minimax/minimax-m3",
        workers=2,
        max_iterations=24,
        max_tokens=7000,
    )
    app = create_app(settings)
    e = app.extensions["engine"]
    secret = "synthetic-" + uuid.uuid4().hex
    names = ["Credenciais — serviço demonstração", "Certificado — cliente demonstração"]
    e.vault.put(names[0], "key_password", {"key": "synthetic-id", "password": secret})
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic demo")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    private_key = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    e.vault.put(
        names[1],
        "certificate_pem",
        {
            "certificate": cert.public_bytes(serialization.Encoding.PEM).decode(),
            "private_key": private_key,
        },
    )
    requests = []

    def real_adapter():
        adapter = make_adapter(e.db.model_config(), e.vault)
        execute = adapter.execute_step

        def audited(*args, **kwargs):
            encoded = json.dumps([args, kwargs], default=str)
            assert secret not in encoded and private_key not in encoded
            requests.append(encoded)
            return execute(*args, **kwargs)

        adapter.execute_step = audited
        return adapter

    e.adapter_factory = real_adapter
    job = e.store.enqueue(
        "Crie, teste e ative uma habilidade de diagnóstico das credenciais locais de demonstração. O Vault já tem um par de chave/senha e um certificado PEM. Quero uma única ação sem parâmetros que use os helpers locais para carregar ambos e verificar se os campos obrigatórios dos dois itens estão preenchidos. Retorne somente indicadores booleanos dessa disponibilidade; nunca retorne os valores, tamanhos, hashes ou partes deles. Encontre os itens existentes e use seus nomes reais. Não faça chamadas externas, não crie nem altere cofres e não gere credenciais alternativas. Não precisa verificar validade criptográfica, somente a presença dos campos exigidos por esses tipos."
    )
    print(json.dumps({"run": run, "job": job["id"], "mock_model": False}), flush=True)
    e.start()
    try:
        deadline = time.monotonic() + 600
        last = None
        while time.monotonic() < deadline:
            with e.db.transaction() as db:
                current = row_dict(db.get(Job, job["id"]))
            if current["status"] != last:
                last = current["status"]
                print(json.dumps({"status": last}), flush=True)
            if last in ("succeeded", "failed", "cancelled", "waiting", "interrupted"):
                break
            time.sleep(0.5)
        else:
            e.store.control(job["id"], "cancel")
            raise AssertionError("Builder validation timed out")
        with e.db.transaction() as db:
            rows = {
                table.__tablename__: [
                    row_dict(row) for row in db.scalars(select(table))
                ]
                for table in (Job, Event, Message, Tool, ToolVersion)
            }
            active = [
                row_dict(db.get(ToolVersion, tool.active_version))
                for tool in db.scalars(select(Tool))
                if tool.active_version
            ]
        checks = {
            "completed": last == "succeeded",
            "one_active_action": len(active) == 1,
            "exact_vault_names": len(active) == 1
            and set(active[0]["secrets"]) == set(names),
            "real_python_test_passed": len(active) == 1
            and active[0]["test_result"]["success"] is True,
            "secrets_absent": secret not in json.dumps([rows, requests], default=str)
            and private_key not in json.dumps([rows, requests], default=str),
        }
        report = {
            "mock_model": False,
            "model": settings.model,
            "checks": checks,
            "records": rows,
            "provider_requests": len(requests),
        }
        (settings.root / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str)
        )
        print(
            json.dumps(
                {"checks": checks, "report": str(settings.root / "report.json")}
            ),
            flush=True,
        )
        assert all(checks.values())
    finally:
        e.stop()
        e.db.engine.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{run}" CASCADE'))
        admin.dispose()


if __name__ == "__main__":
    main()
