"""Instance layout and explicit, offline migration from the legacy layout."""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path

LAYOUT = 1
MARKER = Path("state/storage-layout.json")


def check_layout(root, project):
    marker = root / MARKER
    if marker.exists():
        try:
            if json.loads(marker.read_text())["version"] == LAYOUT:
                return
        except (ValueError, KeyError, TypeError):
            pass
        raise RuntimeError(f"Unsupported or damaged storage layout: {marker}")
    legacy = root
    if root == project / ".fluxyr" and not (root / ".runtime").exists():
        legacy = project
    if (legacy / ".runtime").exists():
        raise RuntimeError(
            f"Legacy Fluxyr storage found at {legacy}. Stop the old server and run "
            f"fluxyr migrate-storage --from '{legacy}' --to '{root}' "
            "(preview), then repeat with --apply. No new database or Vault key was created."
        )


def mark_layout(root):
    marker = root / MARKER
    if not marker.exists():
        descriptor, temporary = tempfile.mkstemp(dir=marker.parent)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump({"version": LAYOUT}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, marker)
            except FileExistsError:
                pass
        finally:
            os.unlink(temporary)


def _regular(path):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"Migration requires a regular file: {path}")


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _copy_file(source, target):
    _regular(source)
    digest = _digest(source)
    shutil.copy2(source, target, follow_symlinks=False)
    if _digest(source) != digest or _digest(target) != digest:
        raise RuntimeError(f"File changed or copy verification failed: {source}")


def _copy_tree(source, target):
    if source.is_symlink() or not source.is_dir():
        raise ValueError(f"Migration requires an ordinary directory: {source}")
    target.mkdir()
    for entry in source.iterdir():
        if entry.is_symlink() or entry.is_mount():
            raise ValueError(
                f"Resolve symlinks/mounted children before migration: {entry}"
            )
        if entry.is_dir():
            _copy_tree(entry, target / entry.name)
        else:
            _copy_file(entry, target / entry.name)
    shutil.copystat(source, target)


@contextmanager
def _offline_fences(source, database_url):
    """Use the same ownership fences as the old workers; never start an engine."""
    import fcntl

    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import SQLAlchemyError

    default_db = source / ".runtime/fluxyr.sqlite3"
    sqlite_paths = {default_db} if default_db.exists() else set()
    url = make_url(database_url) if database_url else None
    if (
        url
        and url.get_backend_name() == "sqlite"
        and url.database not in (None, "", ":memory:")
    ):
        sqlite_paths.add(Path(url.database).resolve())
    with ExitStack() as stack:
        for path in sorted(sqlite_paths):
            _regular(path)
            descriptor = os.open(
                str(path) + ".worker.lock",
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                0o600,
            )
            lock = stack.enter_context(os.fdopen(descriptor, "a+b"))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError(
                    "Stop the Fluxyr worker before migrating storage"
                ) from None
        if url and url.get_backend_name() == "postgresql":
            engine = create_engine(url, connect_args={"connect_timeout": 5})
            stack.callback(engine.dispose)
            try:
                connection = stack.enter_context(engine.connect())
                connection.execute(text("SET statement_timeout = 5000"))
                acquired = connection.scalar(
                    text(
                        "SELECT pg_try_advisory_lock(hashtext(current_schema()), 70399)"
                    )
                )
                connection.commit()
                if not acquired:
                    raise RuntimeError(
                        "Stop the Fluxyr worker before migrating storage"
                    )
                # Disposing the connection pool releases the session advisory lock.
            except SQLAlchemyError:
                raise RuntimeError(
                    "Could not check PostgreSQL worker ownership; verify DATABASE_URL"
                ) from None
        yield


def migrate_storage(source, destination, *, apply=False, database_url=""):
    """Copy durable storage, retaining the source and refusing destination merges.

    Servers (including HTTP-only processes) must be stopped. Cache environments
    contain absolute paths and are deliberately rebuilt instead of relocated.
    PostgreSQL/external DATABASE_URL databases are not moved by this operation.
    """
    source, destination = (
        Path(source).expanduser().resolve(),
        Path(destination).expanduser().resolve(),
    )
    legacy = source / ".runtime"
    if legacy.is_symlink() or not legacy.is_dir():
        raise ValueError(f"No legacy .runtime directory at {source}")
    inplace = source == destination
    if (source / MARKER).exists():
        raise ValueError("The source already uses the new storage layout")
    if not inplace and (
        source.is_relative_to(destination)
        or destination.is_relative_to(legacy)
        or any(
            destination.is_relative_to(source / name) for name in ("data", "workspace")
        )
    ):
        raise ValueError("Destination overlaps the source storage")
    if inplace:
        if any((destination / name).exists() for name in ("state", "cache", "runtime")):
            raise ValueError(
                "Destination state/cache/runtime already exists; refusing to merge"
            )
    elif destination.exists():
        raise ValueError("Destination already exists; choose an unused directory")
    plan = {
        "source": str(source),
        "destination": str(destination),
        "applied": False,
        "files": "Keep data/ and workspace/ in place"
        if inplace
        else "Copy and verify data/ and workspace/",
        "state": "Copy vault.key and backup default SQLite into state/",
        "cache": "Rebuild Python environments; reinstall optional browser runtime if not bundled",
        "database_url": "External DATABASE_URL is unchanged; update explicit legacy SQLite paths separately",
        "originals": "Preserved; do not restart the old instance after switching",
    }
    if not apply:
        return plan
    with _offline_fences(source, database_url):
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(
            tempfile.mkdtemp(
                prefix=".fluxyr-migration-",
                dir=destination if inplace else destination.parent,
            )
        )
        installed = []
        try:
            for name in ("state", "cache", "runtime"):
                (stage / name).mkdir(mode=0o700)
            for name in ("data", "workspace"):
                old = source / name
                if not inplace and old.exists():
                    _copy_tree(old, stage / name)
                elif not inplace:
                    (stage / name).mkdir()
            key = legacy / "vault.key"
            if key.exists() or key.is_symlink():
                _copy_file(key, stage / "state/vault.key")
                (stage / "state/vault.key").chmod(0o600)
            database = legacy / "fluxyr.sqlite3"
            if database.exists():
                _regular(database)
                original = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
                copied = sqlite3.connect(stage / "state/fluxyr.sqlite3")
                try:
                    original.backup(copied)
                    if copied.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                        raise RuntimeError("SQLite integrity verification failed")
                finally:
                    copied.close()
                    original.close()
            checkpoint = legacy / "tmp-cleanup-at"
            if checkpoint.exists():
                _copy_file(checkpoint, stage / "runtime/tmp-cleanup-at")
            if inplace:
                # The marker is last: an interrupted migration cannot look initialized.
                for name in ("cache", "runtime", "state"):
                    target = destination / name
                    (stage / name).rename(target)
                    installed.append(target)
                mark_layout(destination)
            else:
                mark_layout(stage)
                if destination.exists():
                    raise ValueError(
                        "Destination appeared during migration; refusing to overwrite"
                    )
                stage.rename(destination)
            return {**plan, "applied": True}
        except BaseException:
            for path in reversed(installed):
                shutil.rmtree(path)
            raise
        finally:
            if stage.exists():
                shutil.rmtree(stage)


def migration_cli(arguments):
    from dotenv import load_dotenv

    load_dotenv(Path.cwd() / ".env", override=False)
    parser = argparse.ArgumentParser(
        description="Preview or apply an offline storage migration. Stop all Fluxyr processes first."
    )
    parser.add_argument("--from", dest="source", required=True, type=Path)
    parser.add_argument(
        "--to",
        dest="destination",
        type=Path,
        default=Path(os.getenv("FLUXYR_ROOT") or ".fluxyr"),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Copy verified data; retain originals. Without this flag, only show the plan.",
    )
    args = parser.parse_args(arguments)
    try:
        report = migrate_storage(
            args.source,
            args.destination,
            apply=args.apply,
            database_url=os.getenv("DATABASE_URL", ""),
        )
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        parser.exit(2, f"Storage migration failed: {exc}\n")
    print(json.dumps(report, indent=2))
