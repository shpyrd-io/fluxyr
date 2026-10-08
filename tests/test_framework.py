"""Public API exercised through Flask, real DB sessions and the brain executor."""

import json
import threading
from typing import Literal

import pytest
from conftest import ScriptedAdapter, execute_next
from flask import current_app
from pydantic import BaseModel
from sqlalchemy import text

from fluxyr import Fluxyr, __version__
from fluxyr.config import Settings
from fluxyr.models import Configuration


@pytest.fixture
def framework(tmp_path, database_url):
    apps = []

    def make(**options):
        adapter = ScriptedAdapter([])
        app = Fluxyr(
            __name__,
            settings=Settings(
                root=tmp_path / "instance",
                database_url=database_url,
                testing=True,
                **options,
            ),
            adapter_factory=lambda: adapter,
        )
        apps.append(app)
        return app, adapter

    yield make
    for app in apps:
        app.close()


def test_import_and_registration_are_lazy_and_functions_stay_callable(framework):
    before = {t.ident for t in threading.enumerate()}
    app, _ = framework()

    @app.tool()
    def double(value: int = 2) -> dict:
        """Double the given number."""
        return {"value": value * 2}

    @app.get("/api/example/double")
    def endpoint():
        return double()

    assert "engine" not in app.extensions
    assert not app._fluxyr_settings.root.exists()
    assert before == {t.ident for t in threading.enumerate()}
    assert double(3) == {"value": 6}
    client = app.test_client()
    assert client.get("/api/example/double").json == {"value": 4}
    assert not client.get("/api/health").json["worker"]
    assert client.get("/api/health").json["version"] == __version__
    assert client.get("/").status_code == 200
    tool = next(t for t in client.get("/api/tools").json if t["name"] == "double")
    assert tool["parameters"]["properties"]["value"]["default"] == 2
    with pytest.raises(RuntimeError, match="before initialize"):
        app.tool()(double)


def test_native_tool_uses_same_database_context_and_durable_events(framework):
    app, adapter = framework()

    @app.tool(parallel_safe=True, side_effecting=False)
    def answer(value: int) -> dict:
        """Read from the application database."""
        assert current_app._get_current_object() is app
        with app.db.transaction() as db:
            result = db.scalar(text("SELECT :value"), {"value": value})
        return {"answer": result}

    adapter.replies = [[("answer", {"value": 42}), ("answer", {"value": 43})], "Done"]
    app.engine.store.enqueue("Read two values")
    job = execute_next(app.engine)
    assert job["status"] == "succeeded", job["error"]
    events = app.engine.store.events(job["session_id"])
    assert any(
        e["type"] == "tool_batch_start" and e["payload"]["parallel"] for e in events
    )
    results = [
        e["payload"]["result"]["output"]["answer"]
        for e in events
        if e["type"] == "tool_end"
    ]
    assert sorted(results) == [42, 43]


class Address(BaseModel):
    city: str


def test_nested_schema_defaults_and_strict_types(framework):
    app, adapter = framework()
    called = []

    @app.tool(description="Get a forecast")
    def forecast(address: Address, units: Literal["c", "f"] = "c") -> dict:
        called.append(address.city)
        return {"city": address.city, "units": units}

    adapter.replies = [[("forecast", {"address": {"city": "Recife"}})], "Done"]
    app.engine.store.enqueue("Forecast")
    job = execute_next(app.engine)
    assert job["status"] == "succeeded", job["error"]
    assert called == ["Recife"]
    adapter.replies = [[("forecast", {"address": {"city": 123}})], "Done"]
    app.engine.store.enqueue("Invalid forecast")
    job = execute_next(app.engine)
    assert called == ["Recife"]
    result = next(
        e["payload"]["result"]
        for e in app.engine.store.events(job["session_id"])
        if e["job_id"] == job["id"] and e["type"] == "tool_end"
    )
    assert result["executed"] is False and result["type"] == "validation_error"


def test_file_skills_loaded_readonly_without_copying_to_database(framework, tmp_path):
    folder = tmp_path / "skills"
    folder.mkdir()
    (folder / "nested").mkdir()
    (folder / "weather.md").write_text(
        "---\nname: weather\ndescription: Forecast\n---\nUse forecast for São Paulo.",
        encoding="utf-8",
    )
    (folder / "nested" / "notes.md").write_text("Always cite real execution results.")
    app, adapter = framework(skills_dir=str(folder))
    client = app.test_client()
    skills = client.get("/api/skills").json
    assert len(skills) == 2 and all(s["readonly"] for s in skills)
    assert (
        client.get("/api/skills/file:nested/notes.md").json["instruction"]
        == "Always cite real execution results."
    )
    weather = next(s for s in skills if s["name"] == "weather")
    assert weather["id"] == "file:weather.md" and "São Paulo" in weather["instruction"]
    assert (
        client.patch(
            "/api/skills/file:weather.md", json={"instruction": "overwrite"}
        ).status_code
        == 400
    )
    assert client.delete("/api/skills/file:weather.md").status_code == 400
    assert client.post("/api/skills/file:weather.md/build", json={}).status_code == 400
    assert (
        client.post(
            "/api/skills",
            json={"name": "weather", "description": "x", "instruction": "x"},
        ).status_code
        == 400
    )
    app.engine.store.enqueue("Read instructions")
    job = app.engine.store.claim(app.engine.owner)
    assert len(job["snapshot"]["skills"]) == 2
    app.engine.execute(job)
    assert "Use forecast for São Paulo." in json.dumps(
        adapter.calls, ensure_ascii=False
    ) or "Use forecast for São Paulo." in str(job["snapshot"])


@pytest.mark.parametrize("name", ["list_skills", "save_in_memory"])
def test_reserved_tool_names_fail_before_serving(framework, name):
    app, _ = framework()

    @app.tool(name=name, description="Collision")
    def fn() -> dict:
        return {}

    with pytest.raises(ValueError, match="conflict"):
        app.initialize()


def test_endpoint_collision_fails_explicitly(framework):
    app, _ = framework()

    @app.get("/api/health")
    def health():
        return {}

    with pytest.raises(ValueError, match="route conflicts"):
        app.initialize()


def test_environment_wins_over_old_database_settings(framework, monkeypatch):
    monkeypatch.setenv("FLUXYR_PROVIDER", "openrouter")
    monkeypatch.setenv("FLUXYR_MODEL", "minimax/minimax-m3")
    monkeypatch.setenv("FLUXYR_MAX_TOKENS", "2048")
    app, _ = framework()
    with app.db.transaction() as db:
        db.add(Configuration(key="model", value={"provider": "old", "model": "old"}))
    config = app.test_client().get("/api/settings").json
    assert config["model"] == "minimax/minimax-m3" and config["max_tokens"] == 2048
    assert config["managed_by"] == "environment"
    assert (
        app.test_client().post("/api/settings", json={"model": "other"}).status_code
        == 405
    )
    assert app.db.model_config()["model"] == "minimax/minimax-m3"


def test_missing_required_environment_and_invalid_limits(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings(root=tmp_path)
    settings.prepare()
    assert settings.database_url == "sqlite:///" + str(
        tmp_path / "state/fluxyr.sqlite3"
    )
    monkeypatch.setenv("FLUXYR_TOOL_WORKERS", "invalid")
    with pytest.raises(ValueError, match="FLUXYR_TOOL_WORKERS"):
        Settings()


def test_worker_lifecycle_is_explicit_and_start_idempotent(framework):
    app, _ = framework()
    app.initialize()
    assert app.engine.thread is None
    app.start()
    thread = app.engine.thread
    app.start()
    assert app.engine.thread is thread and thread.is_alive()
    app.close()
    assert not thread.is_alive()
    app.close()


def test_native_tool_failure_returns_false_and_is_persisted(framework):
    app, adapter = framework()

    @app.tool(description="Fail for validation")
    def fail() -> dict:
        raise RuntimeError("native failure")

    adapter.replies = [[("fail", {})], "Failed"]
    app.engine.store.enqueue("Fail")
    job = execute_next(app.engine)
    result = next(
        e["payload"]["result"]
        for e in app.engine.store.events(job["session_id"])
        if e["type"] == "tool_end"
    )
    assert result["done"] is True and result["success"] is False
    assert result["error"] == "native failure"


def test_dotenv_and_relative_root_are_resolved_from_working_directory(
    tmp_path, database_url, monkeypatch
):
    project = tmp_path / "consumer"
    project.mkdir()
    monkeypatch.chdir(project)
    keys = ["DATABASE_URL", "FLUXYR_ROOT", "FLUXYR_PROVIDER", "FLUXYR_MAX_TOKENS"]
    for key in keys:
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)
    (project / ".env").write_text(
        f"DATABASE_URL={database_url}\nFLUXYR_ROOT=instance\nFLUXYR_PROVIDER=openrouter\nFLUXYR_MAX_TOKENS=2048\n"
    )
    monkeypatch.setenv("FLUXYR_MAX_TOKENS", "3072")
    app = Fluxyr(
        __name__, root_path=str(project), adapter_factory=lambda: ScriptedAdapter([])
    )
    try:
        app.initialize()
        assert app.engine.settings.root == project / "instance"
        assert app.db.model_config()["max_tokens"] == 3072
        assert app.engine.thread is None
    finally:
        app.close()
