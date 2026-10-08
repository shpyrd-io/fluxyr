"""Real layout migration: preserve encrypted state, files and SQLite WAL data."""

import fcntl
import json
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from fluxyr.config import Settings
from fluxyr.file_skills import load_file_skills
from fluxyr.storage import MARKER, migrate_storage


def test_postgres_configuration_and_file_migration_without_sqlite(tmp_path):
    # A fresh interpreter reproduces slim hosts whose _sqlite3 cannot load.
    script = textwrap.dedent("""
        import builtins
        import sys
        from pathlib import Path

        original_import = builtins.__import__
        def without_sqlite(name, *args, **kwargs):
            if name.split('.')[0] in ('sqlite3', '_sqlite3'):
                raise ImportError('libsqlite3.so.0: cannot open shared object file')
            return original_import(name, *args, **kwargs)
        builtins.__import__ = without_sqlite

        from fluxyr import Fluxyr
        from fluxyr.config import Settings
        from fluxyr.storage import migrate_storage, migration_cli

        root = Path(sys.argv[1])
        settings = Settings(root=root / 'instance', project=root,
                            database_url='postgresql+psycopg://localhost/unused')
        settings.prepare()
        assert settings.database_url.startswith('postgresql')
        legacy = root / 'legacy'
        (legacy / '.runtime').mkdir(parents=True)
        (legacy / '.runtime/vault.key').write_text('preserve-key')
        migrated = root / 'migrated'
        assert migrate_storage(legacy, migrated, apply=True)['applied']
        assert (migrated / 'state/vault.key').read_text() == 'preserve-key'
        assert 'sqlite3' not in sys.modules

        # CLI errors must also work without importing SQLite in an except clause.
        try:
            migration_cli(['--from', str(root / 'absent'), '--to', str(root / 'unused')])
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError('Invalid migration should fail')
    """)
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_sqlite_backup_failure_keeps_original_and_reports_cli_error(
    tmp_path, monkeypatch, capsys
):
    from fluxyr.storage import migration_cli

    runtime = tmp_path / "legacy/.runtime"
    runtime.mkdir(parents=True)
    database = runtime / "fluxyr.sqlite3"
    database.write_bytes(b"not a sqlite database")
    destination = tmp_path / "destination"
    monkeypatch.setenv("DATABASE_URL", "")
    with pytest.raises(SystemExit) as error:
        migration_cli(
            ["--from", str(runtime.parent), "--to", str(destination), "--apply"]
        )
    assert error.value.code == 2
    assert "SQLite backup failed" in capsys.readouterr().err
    assert database.read_bytes() == b"not a sqlite database"
    assert not destination.exists()
    assert not list(tmp_path.glob(".fluxyr-migration-*"))


def legacy_instance(root):
    runtime = root / ".runtime"
    runtime.mkdir(parents=True)
    key = Fernet.generate_key()
    (runtime / "vault.key").write_bytes(key)
    with sqlite3.connect(runtime / "fluxyr.sqlite3") as db:
        db.execute("CREATE TABLE credentials (secret BLOB)")
        db.execute(
            "INSERT INTO credentials VALUES (?)",
            (Fernet(key).encrypt(b"migration-sentinel"),),
        )
    (root / "data/tmp/browser").mkdir(parents=True)
    (root / "data/tmp/browser/capture.webp").write_bytes(b"file-sentinel")
    (root / "workspace/job").mkdir(parents=True)
    (root / "workspace/job/action.py").write_text("print('hello')")
    (runtime / "envs").mkdir()
    (runtime / "envs/stale-path").write_text(str(root))
    return runtime


def test_default_root_and_project_skills_are_independent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FLUXYR_ROOT", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills/hello.md").write_text("Use this local skill.")
    settings = Settings(skills_dir="skills")
    settings.prepare()
    assert settings.root == tmp_path / ".fluxyr"
    assert {p.name for p in settings.root.iterdir()} == {
        "state",
        "data",
        "runtime",
        "cache",
        "workspace",
    }
    assert (
        Path(settings.database_url.removeprefix("sqlite:///"))
        == settings.state / "fluxyr.sqlite3"
    )
    assert load_file_skills(settings)[0]["name"] == "hello"
    monkeypatch.setenv("FLUXYR_ROOT", str(tmp_path / "mounted-volume"))
    mounted = Settings(skills_dir="skills")
    mounted.prepare()
    assert load_file_skills(mounted) == load_file_skills(settings)


@pytest.mark.parametrize("inplace", [False, True])
def test_migration_preserves_files_key_and_sqlite_wal(tmp_path, inplace):
    source = tmp_path / "legacy"
    runtime = legacy_instance(source)
    destination = source if inplace else source / ".fluxyr"
    # Keep a WAL open with committed data absent from the main database file.
    db = sqlite3.connect(runtime / "fluxyr.sqlite3")
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE recent (value TEXT)")
        db.execute("INSERT INTO recent VALUES ('wal-sentinel')")
        db.commit()
        plan = migrate_storage(source, destination)
        assert not plan["applied"]
        assert not (destination / "state").exists()
        assert migrate_storage(source, destination, apply=True)["applied"]
        key = (destination / "state/vault.key").read_bytes()
        assert key == (runtime / "vault.key").read_bytes()
        with sqlite3.connect(destination / "state/fluxyr.sqlite3") as migrated:
            assert (
                Fernet(key).decrypt(
                    migrated.execute("SELECT secret FROM credentials").fetchone()[0]
                )
                == b"migration-sentinel"
            )
            assert migrated.execute("SELECT value FROM recent").fetchone() == (
                "wal-sentinel",
            )
        assert (
            destination / "data/tmp/browser/capture.webp"
        ).read_bytes() == b"file-sentinel"
        assert (destination / "workspace/job/action.py").read_text() == "print('hello')"
        assert (runtime / "envs/stale-path").exists()
        assert not (destination / "cache/envs").exists()
        assert json.loads((destination / MARKER).read_text())["version"] == 1
        Settings(root=destination).prepare()
    finally:
        db.close()


def test_legacy_startup_requires_explicit_migration(tmp_path, monkeypatch):
    legacy_instance(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FLUXYR_ROOT", raising=False)
    for settings in (Settings(), Settings(root=tmp_path)):
        with pytest.raises(RuntimeError, match="migrate-storage"):
            settings.prepare()
    assert not (tmp_path / ".fluxyr").exists()
    assert not (tmp_path / "state").exists()


def test_active_worker_prevents_copy(tmp_path):
    runtime = legacy_instance(tmp_path)
    with (runtime / "fluxyr.sqlite3.worker.lock").open("wb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="Stop the Fluxyr worker"):
            migrate_storage(tmp_path, tmp_path / ".fluxyr", apply=True)
    assert not (tmp_path / ".fluxyr").exists()


def test_copy_error_keeps_originals_and_does_not_publish_partial_instance(tmp_path):
    legacy_instance(tmp_path)
    (tmp_path / "data/escape").symlink_to(tmp_path / ".runtime/vault.key")
    with pytest.raises(ValueError, match="symlinks"):
        migrate_storage(tmp_path, tmp_path / ".fluxyr", apply=True)
    assert not (tmp_path / ".fluxyr").exists()
    assert (tmp_path / ".runtime/vault.key").exists()
    assert not list(tmp_path.glob(".fluxyr-migration-*"))


def test_migration_never_merges_or_overwrites_destination(tmp_path):
    legacy_instance(tmp_path)
    destination = tmp_path / ".fluxyr"
    destination.mkdir()
    (destination / "keep").write_text("untouched")
    with pytest.raises(ValueError, match="already exists"):
        migrate_storage(tmp_path, destination, apply=True)
    assert (destination / "keep").read_text() == "untouched"


@pytest.mark.parametrize("child", ["data", "workspace", ".runtime"])
def test_migration_rejects_recursive_destinations(tmp_path, child):
    legacy_instance(tmp_path)
    with pytest.raises(ValueError, match="overlaps"):
        migrate_storage(tmp_path, tmp_path / child / "nested", apply=True)


@pytest.mark.integration
def test_migration_honors_postgres_worker_ownership(tmp_path, database_url):
    from sqlalchemy import create_engine, text

    if not database_url.startswith("postgresql"):
        pytest.skip("requires disposable PostgreSQL database")
    legacy_instance(tmp_path)
    engine = create_engine(database_url)
    try:
        with engine.connect() as worker:
            worker.execute(
                text("SELECT pg_advisory_lock(hashtext(current_schema()), 70399)")
            )
            worker.commit()
            with pytest.raises(RuntimeError, match="Stop the Fluxyr worker"):
                migrate_storage(
                    tmp_path,
                    tmp_path / ".fluxyr",
                    apply=True,
                    database_url=database_url,
                )
            worker.execute(
                text("SELECT pg_advisory_unlock(hashtext(current_schema()), 70399)")
            )
            worker.commit()
        assert migrate_storage(
            tmp_path, tmp_path / ".fluxyr", apply=True, database_url=database_url
        )["applied"]
        with engine.connect() as worker:
            assert worker.scalar(
                text("SELECT pg_try_advisory_lock(hashtext(current_schema()), 70399)")
            )
    finally:
        engine.dispose()
