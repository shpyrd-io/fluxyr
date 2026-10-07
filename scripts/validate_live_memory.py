"""Real-model memory probe; artificial fixture, isolated PostgreSQL schema."""

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from fluxyr.config import Settings
from fluxyr.engine import Engine
from fluxyr.models import Job, Session, Message
from sqlalchemy import select


def main():
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env", override=True)
    base = Settings()
    run_id = uuid.uuid4().hex[:12]
    schema = "memory_validation_" + run_id
    with create_engine(base.database_url).begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = make_url(base.database_url).update_query_dict(
        {"options": f"-csearch_path={schema}"}
    )
    settings = Settings(
        database_url=url.render_as_string(hide_password=False),
        root=root / ".runtime/live-validation" / run_id,
        workers=1,
    )
    e = Engine(settings)
    first = e.store.enqueue(
        "Teste com dados fictícios: salve na memória semântica, chave carteira_teste, que o saldo é exatamente R$ 127,43. Confirme a gravação.",
        None,
    )
    e.execute(e.store.claim(e.owner))
    report = {
        "mock_model": False,
        "model": e.db.model_config()["model"],
        "run_id": run_id,
    }
    with e.db.transaction() as s:
        session = s.get(Session, first["session_id"])
        assert s.get(Job, first["id"]).status == "succeeded"
        state = dict(session.brain)
        semantic = state["semantic"]
        assert "127,43" in json.dumps(semantic, ensure_ascii=False), (
            "Value was not saved"
        )
        # Remove the conversational path and every other memory layer. The recall
        # can only come from the persisted semantic store, restored in a NEW engine.
        state["short_term"] = {
            **state["short_term"],
            "items": [
                m for m in state["short_term"]["items"] if m.get("role") == "system"
            ],
        }
        state["episodic"] = {**state["episodic"], "items": []}
        state["implicit"] = {**state["implicit"], "patterns": {}}
        session.brain = state
    e.db.engine.dispose()
    e = Engine(settings)
    second = e.store.enqueue(
        "Qual é o saldo da carteira_teste salvo na memória? Apenas consulte. Responda o valor exato, sem gravar ou alterar nada.",
        first["session_id"],
    )
    e.execute(e.store.claim(e.owner))
    with e.db.transaction() as s:
        job = s.get(Job, second["id"])
        answer = s.scalar(
            select(Message.content).where(
                Message.job_id == job.id, Message.role == "assistant"
            )
        )
        assert job.status == "succeeded", job.error
        assert answer and "127,43" in answer, answer
        report.update(
            success=True,
            answer=answer,
            restored_in_new_engine=True,
            short_term_cleared=True,
        )
    (settings.root / "memory-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print(json.dumps(report, ensure_ascii=False))
    e.db.engine.dispose()


if __name__ == "__main__":
    main()
