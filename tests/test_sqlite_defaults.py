"""Default consumer startup and SQLite's real transaction/worker ownership."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import ScriptedAdapter
from sqlalchemy import select

from fluxyr import Fluxyr
from fluxyr.config import Settings
from fluxyr.database import Database
from fluxyr.models import Job


@pytest.fixture(autouse=True)
def clean_default_environment(monkeypatch):
    for key in (
        "DATABASE_URL",
        "FLUXYR_ROOT",
        "FLUXYR_PROVIDER",
        "FLUXYR_MODEL",
        "OPENROUTER_API_KEY",
        "PORT",
    ):
        # Register restoration even if dotenv adds a previously absent variable.
        monkeypatch.setenv(key, "")


def test_only_model_and_openrouter_key_are_required(monkeypatch, tmp_path):
    for key in (
        "DATABASE_URL",
        "FLUXYR_ROOT",
        "FLUXYR_PROVIDER",
        "FLUXYR_MODEL",
        "OPENROUTER_API_KEY",
        "PORT",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    settings = Settings()
    settings.prepare()
    assert settings.root == tmp_path
    assert settings.provider == "openrouter"
    assert settings.port == 5050
    assert settings.database_url == "sqlite:///" + str(
        tmp_path / ".runtime/fluxyr.sqlite3"
    )
    with pytest.raises(ValueError, match="FLUXYR_MODEL"):
        settings.validate_credentials()
    settings.model = "chosen-model"
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        settings.validate_credentials()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    settings.validate_credentials()


def test_sqlite_worker_executes_and_reopens_persisted_state(tmp_path):
    settings = Settings(root=tmp_path, model="scripted")
    app = Fluxyr(
        __name__,
        settings=settings,
        adapter_factory=lambda: ScriptedAdapter(["SQLite works"]),
    )
    try:
        app.start()
        job = app.engine.store.enqueue("Hello")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with app.db.transaction() as s:
                status = s.get(Job, job["id"]).status
            if status == "succeeded":
                break
            time.sleep(0.02)
        assert status == "succeeded"
        assert app.test_client().get("/api/health").json["worker"]
        with app.db.engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
    finally:
        app.close()
    reopened = Fluxyr(
        __name__,
        settings=Settings(root=tmp_path),
        adapter_factory=lambda: ScriptedAdapter([]),
    )
    try:
        with reopened.db.transaction() as s:
            assert s.get(Job, job["id"]).status == "succeeded"
    finally:
        reopened.close()


def test_sqlite_claims_are_atomic_across_database_connections(tmp_path):
    from fluxyr.store import Store

    settings = Settings(root=tmp_path)
    settings.prepare()
    databases = [Database(settings), Database(settings)]
    try:
        for db in databases:
            db.initialize()
        stores = [Store(db) for db in databases]
        queued = stores[0].enqueue("Only once")
        ready = threading.Barrier(2)

        def claim(index):
            ready.wait()
            return stores[index].claim(str(index))

        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(claim, (0, 1)))
        claimed = [job for job in results if job]
        assert [job["id"] for job in claimed] == [queued["id"]]
    finally:
        for db in databases:
            db.engine.dispose()


def test_sqlite_rejects_second_worker_and_releases_fence_on_close(tmp_path):
    def make():
        return Fluxyr(
            __name__,
            settings=Settings(root=tmp_path),
            adapter_factory=lambda: ScriptedAdapter([]),
        )

    first, second = make(), make()
    try:
        first.start()
        with pytest.raises(RuntimeError, match="already owns this SQLite"):
            second.start()
        first.close()
        second.start()
        assert second.engine.thread.is_alive()
    finally:
        first.close()
        second.close()


def test_sqlite_nested_transaction_rollback(tmp_path):
    settings = Settings(root=tmp_path)
    settings.prepare()
    db = Database(settings)
    db.initialize()
    try:
        from fluxyr.models import Configuration

        with db.transaction() as s:
            s.add(Configuration(key="outer", value=1))
            with pytest.raises(ValueError), db.transaction() as inner:
                inner.add(Configuration(key="inner", value=2))
                raise ValueError("rollback savepoint")
        with db.transaction() as s:
            assert [r.key for r in s.scalars(select(Configuration))] == ["outer"]
    finally:
        db.engine.dispose()


def test_runtime_root_and_dotenv_follow_launch_directory(monkeypatch, tmp_path):
    code = tmp_path / "code"
    launch = tmp_path / "launch"
    code.mkdir()
    launch.mkdir()
    for key in ("FLUXYR_MODEL", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(key)
    (launch / ".env").write_text(
        "FLUXYR_MODEL=chosen-model\nOPENROUTER_API_KEY=test-key\n"
    )
    monkeypatch.chdir(launch)
    app = Fluxyr(
        __name__, root_path=str(code), adapter_factory=lambda: ScriptedAdapter([])
    )
    try:
        app.initialize()
        assert app.engine.settings.root == launch
        assert app.engine.settings.model == "chosen-model"
        assert (launch / ".runtime/fluxyr.sqlite3").exists()
        assert not (code / ".runtime").exists()
    finally:
        app.close()


def test_sqlite_oauth_network_wait_does_not_block_database_or_overwrite_edits(
    tmp_path, monkeypatch
):
    app = Fluxyr(
        __name__,
        settings=Settings(root=tmp_path),
        adapter_factory=lambda: ScriptedAdapter([]),
    )
    entered, release = threading.Event(), threading.Event()
    credentials = {
        "grant_type": "client_credentials",
        "client_id": "id",
        "client_secret": "secret",
        "token_url": "https://example.invalid/token",
    }
    try:
        app.engine.vault.put("oauth", "oauth2", credentials)

        def renew(content, data):
            entered.set()
            assert release.wait(3)
            return {
                **content,
                "access_token": "new-token",
                "expires_at": time.time() + 3600,
            }

        monkeypatch.setattr(app.engine.vault, "_token", renew)
        with ThreadPoolExecutor(2) as pool:
            token = pool.submit(app.engine.vault.resolve, "oauth")
            assert entered.wait(2)
            # A separate thread must be able to read/write while HTTPS is pending.
            edit = pool.submit(
                app.engine.vault.put,
                "oauth",
                "oauth2",
                {**credentials, "client_id": "edited"},
            )
            try:
                edit.result(timeout=2)
            finally:
                release.set()
            with pytest.raises(ValueError, match="changed during token renewal"):
                token.result(timeout=2)
        assert app.engine.vault.get_optional("oauth")["client_id"] == "edited"
    finally:
        release.set()
        app.close()
