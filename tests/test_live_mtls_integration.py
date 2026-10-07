"""Explicit opt-in: a real model builds and runs a synthetic OAuth + mTLS API integration."""

import json
import os
import time
from pathlib import Path

import pytest

from cryptography.hazmat.primitives import serialization
from dotenv import load_dotenv
from sqlalchemy import select
from test_vault_oauth_mtls import mtls_server  # noqa: F401 - shared real TLS fixture

from fluxyr.database import row_dict
from fluxyr.models import Event, Job, Tool, ToolVersion
from fluxyr.providers import make_adapter

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.getenv("FLUXYR_TEST_LIVE_MTLS") != "1", reason="Explicit real-model opt-in"
)
def test_model_builds_oauth_mtls_integration(make_app, mtls_server):  # noqa: F811 - pytest fixture injection
    repo = Path(__file__).resolve().parents[1]
    load_dotenv(repo / ".env")
    url, key, cert, calls = mtls_server
    _, engine, _ = make_app(
        provider="openrouter",
        model="minimax/minimax-m3",
        workers=2,
        max_iterations=24,
        max_tokens=7000,
    )
    private_key = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    certificate = engine.vault.put(
        "Certificado demonstração",
        "certificate_pem",
        {
            "certificate": cert.public_bytes(serialization.Encoding.PEM).decode(),
            "private_key": private_key,
        },
    )
    engine.vault.put(
        "Serviço demonstração",
        "oauth2",
        {
            "client_id": "private-client-id",
            "client_secret": "private-client-secret",
            "grant_type": "client_credentials",
            "token_url": url,
            "certificate_id": certificate["id"],
            "scope": "balance.read",
        },
    )
    request_count = 0

    def audited_adapter():
        nonlocal request_count
        adapter = make_adapter(engine.db.model_config(), engine.vault)
        execute = adapter.execute_step

        def step(*args, **kwargs):
            nonlocal request_count
            encoded = json.dumps([args, kwargs], default=str)
            assert all(
                value not in encoded
                for value in (private_key, "private-client-secret", "private-token-")
            )
            request_count += 1
            return execute(*args, **kwargs)

        adapter.execute_step = step
        return adapter

    engine.adapter_factory = audited_adapter
    job = engine.store.enqueue(
        f"Crie, teste e ative uma habilidade com uma única ação para consultar o saldo da API HTTPS de demonstração em {url.removesuffix('/token')}/balance. "
        "É uma API local sintética; GET sem parâmetros, resposta JSON com disponivel (string). "
        "Usa OAuth client_credentials com Bearer token e mTLS tanto no token quanto na consulta. "
        "As credenciais e o certificado já estão no Vault, use os existentes. "
        "A CA de teste já está configurada no ambiente REQUESTS_CA_BUNDLE; mantenha a verificação TLS ligada. "
        "Quero poder chamar a ação e receber o saldo, sem criar credenciais ou pedir login no browser."
    )
    engine.start()
    try:
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            with engine.db.transaction() as db:
                status = db.get(Job, job["id"]).status
            if status in {"succeeded", "failed", "waiting", "cancelled", "interrupted"}:
                break
            time.sleep(0.5)
        else:
            engine.store.control(job["id"], "cancel")
            raise AssertionError("Real-model mTLS validation timed out")
        with engine.db.transaction() as db:
            tools = [row_dict(v) for v in db.scalars(select(Tool))]
            versions = [row_dict(v) for v in db.scalars(select(ToolVersion))]
            events = [
                row_dict(v)
                for v in db.scalars(select(Event).where(Event.type == "tool_end"))
            ]
        report = {
            "status": status,
            "model": "minimax/minimax-m3",
            "provider_requests": request_count,
            "tools": tools,
            "versions": versions,
            "tool_results": events,
            "https_resource_calls": [v for v in calls if "path" in v],
        }
        encoded = json.dumps(report, ensure_ascii=False, indent=2, default=str)
        assert all(
            value not in encoded
            for value in (private_key, "private-client-secret", "private-token-")
        )
        path = repo / ".runtime/live-validation" / f"mtls-{job['id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(encoded)
        print(f"Report: {path}; {request_count} real model requests")
        assert status == "succeeded"
        assert len(tools) == 1 and tools[0]["active_version"]
        assert len(versions) == 1, (
            "The simple integration should not require repeated code generation"
        )
        assert versions[0]["test_result"]["success"] is True
        assert versions[0]["test_result"]["output"]["disponivel"] == "1234.56"
        assert any(v.get("authorized") and v.get("peer") for v in calls if "path" in v)
        names = [v["payload"].get("tool_name") for v in events]
        assert "manage_vault_credential" not in names and "ask_human" not in names
    finally:
        engine.stop()
